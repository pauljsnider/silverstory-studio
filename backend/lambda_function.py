import base64
import json
import os
import uuid
from datetime import datetime
from urllib.parse import urlparse
from urllib.request import urlopen

import boto3

REGION = os.getenv('AWS_REGION', 'us-east-1')
BUCKET_NAME = os.getenv('APP_BUCKET', '').strip()
PRESIGN_TTL_SECONDS = int(os.getenv('PRESIGN_TTL_SECONDS', '3600'))
TRANSCRIBE_LANGUAGE_CODE = os.getenv('TRANSCRIBE_LANGUAGE_CODE', 'en-US')
TRANSCRIBE_JOB_PREFIX = os.getenv('TRANSCRIBE_JOB_PREFIX', 'storyscribe')
TRANSCRIBE_OUTPUT_PREFIX = os.getenv('TRANSCRIBE_OUTPUT_PREFIX', 'transcribe')
ALLOWED_ORIGIN = os.getenv('ALLOWED_ORIGIN', '*')

if not BUCKET_NAME:
    raise RuntimeError('Missing required environment variable APP_BUCKET')

s3 = boto3.client('s3', region_name=REGION)
transcribe = boto3.client('transcribe', region_name=REGION)


def _json(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
            'Access-Control-Allow-Headers': '*',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS'
        },
        'body': json.dumps(body)
    }


def _safe_name(name):
    if not name:
        return 'upload.bin'
    cleaned = ''.join(ch for ch in name if ch.isalnum() or ch in ('-', '_', '.', ' ')).strip()
    return cleaned or 'upload.bin'


def _decode_body(event):
    body = event.get('body', '')
    if event.get('isBase64Encoded', False):
        return base64.b64decode(body)
    try:
        return base64.b64decode(body, validate=True)
    except Exception:
        return body.encode('utf-8')


def _parse_s3_uri(uri):
    if not uri:
        return None
    if uri.startswith('s3://'):
        p = uri.replace('s3://', '', 1)
        i = p.find('/')
        if i <= 0:
            return None
        return {'bucket': p[:i], 'key': p[i + 1:]}
    u = urlparse(uri)
    if not u.netloc:
        return None
    parts = u.path.lstrip('/').split('/')
    if not parts:
        return None
    return {'bucket': parts[0], 'key': '/'.join(parts[1:])}


def _save_manifest(story_id, body):
    body['updatedAt'] = datetime.utcnow().isoformat() + 'Z'
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=f'stories/{story_id}/manifest.json',
        Body=json.dumps(body).encode('utf-8'),
        ContentType='application/json'
    )


def _load_manifest(story_id):
    obj = s3.get_object(Bucket=BUCKET_NAME, Key=f'stories/{story_id}/manifest.json')
    return json.loads(obj['Body'].read())


def _build_segments_from_items(items, max_words=14):
    segments = []
    words = []
    seg_start = None
    for item in items:
        if item.get('type') != 'pronunciation':
            continue
        start = float(item.get('start_time', 0) or 0)
        end = float(item.get('end_time', 0) or start)
        token = (item.get('alternatives') or [{}])[0].get('content', '').strip()
        if not token:
            continue
        if seg_start is None:
            seg_start = start
        words.append(token)
        if len(words) >= max_words:
            segments.append({
                'BeginAudioTime': seg_start,
                'EndAudioTime': end,
                'Content': ' '.join(words)
            })
            words = []
            seg_start = None

    if words:
        end_time = segments[-1]['EndAudioTime'] if segments else (seg_start or 0)
        segments.append({
            'BeginAudioTime': seg_start or 0,
            'EndAudioTime': end_time,
            'Content': ' '.join(words)
        })

    return segments


def _fetch_transcript_from_transcribe(job):
    tx_url = (((job or {}).get('Transcript') or {}).get('TranscriptFileUri'))
    if not tx_url:
        return {'fullText': '', 'segments': []}

    with urlopen(tx_url, timeout=20) as resp:
        payload = json.loads(resp.read().decode('utf-8'))

    full_text = (((payload.get('results') or {}).get('transcripts') or [{}])[0].get('transcript', '') or '').strip()
    items = ((payload.get('results') or {}).get('items') or [])
    segments = _build_segments_from_items(items)
    return {'fullText': full_text, 'segments': segments}


def _refresh_story_manifest(story_id):
    manifest = _load_manifest(story_id)
    job_name = manifest.get('jobName')

    if not job_name:
        manifest['processingStatus'] = manifest.get('processingStatus', 'ready')
        _save_manifest(story_id, manifest)
        return manifest

    try:
        response = transcribe.get_transcription_job(TranscriptionJobName=job_name)
        job = response.get('TranscriptionJob', {})
    except Exception as e:
        manifest['processingStatus'] = 'failed'
        manifest['processingStage'] = 'transcription'
        manifest['processingError'] = str(e)
        _save_manifest(story_id, manifest)
        return manifest

    status = job.get('TranscriptionJobStatus')
    manifest['transcribeStatus'] = status

    if status == 'FAILED':
        manifest['processingStatus'] = 'failed'
        manifest['processingStage'] = 'transcription'
        manifest['processingError'] = job.get('FailureReason', 'Transcription failed')
        _save_manifest(story_id, manifest)
        return manifest

    if status != 'COMPLETED':
        manifest['processingStatus'] = 'transcribing'
        manifest['processingStage'] = 'transcription'
        _save_manifest(story_id, manifest)
        return manifest

    if manifest.get('transcriptText'):
        manifest['processingStatus'] = 'ready'
        manifest['processingStage'] = 'complete'
        _save_manifest(story_id, manifest)
        return manifest

    transcript = _fetch_transcript_from_transcribe(job)
    manifest['transcriptText'] = transcript.get('fullText', '')
    manifest['transcriptSegments'] = transcript.get('segments', [])
    manifest['processingStatus'] = 'ready'
    manifest['processingStage'] = 'complete'
    manifest['processingError'] = ''
    manifest['transcriptCompletedAt'] = datetime.utcnow().isoformat() + 'Z'
    _save_manifest(story_id, manifest)
    return manifest


def _delete_story(story_id):
    to_delete = []
    listed = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=f'stories/{story_id}/')
    to_delete.extend([{'Key': x['Key']} for x in listed.get('Contents', [])])

    manifest = {}
    try:
        manifest = _load_manifest(story_id)
    except Exception:
        pass

    audio_key = manifest.get('audioKey')
    if audio_key:
        to_delete.append({'Key': audio_key})

    job_name = manifest.get('jobName')
    if job_name:
        tx_prefix = f'{TRANSCRIBE_OUTPUT_PREFIX.rstrip('/')}/{job_name}/'
        tx_listed = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=tx_prefix)
        to_delete.extend([{'Key': x['Key']} for x in tx_listed.get('Contents', [])])

    if to_delete:
        uniq = {x['Key']: x for x in to_delete}
        s3.delete_objects(Bucket=BUCKET_NAME, Delete={'Objects': list(uniq.values())})

    return len(to_delete)


def lambda_handler(event, context):
    path = event.get('rawPath', event.get('path', ''))
    method = event.get('requestContext', {}).get('http', {}).get('method', event.get('httpMethod', ''))

    if method == 'OPTIONS':
        return _json(200, {'ok': True})

    try:
        if path == '/api/slideshow/create-upload-url' and method == 'POST':
            body = json.loads(event.get('body', '{}'))
            story_id = body.get('slideshowId')
            filename = _safe_name(body.get('filename', 'upload.bin'))
            mime_type = body.get('mimeType', 'application/octet-stream')
            if not story_id:
                return _json(400, {'error': 'Missing slideshowId'})

            ext = os.path.splitext(filename)[1] or ''
            key = f'stories/{story_id}/media/{uuid.uuid4().hex}{ext}'
            put_url = s3.generate_presigned_url(
                ClientMethod='put_object',
                Params={'Bucket': BUCKET_NAME, 'Key': key, 'ContentType': mime_type},
                ExpiresIn=PRESIGN_TTL_SECONDS
            )
            return _json(200, {'key': key, 'mimeType': mime_type, 'uploadUrl': put_url})

        if path == '/api/upload' and method == 'POST':
            audio_data = _decode_body(event)
            stamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
            audio_key = f'audio/{uuid.uuid4().hex}-{stamp}.webm'
            s3.put_object(Bucket=BUCKET_NAME, Key=audio_key, Body=audio_data, ContentType='audio/webm')

            job_name = f"{TRANSCRIBE_JOB_PREFIX}-{stamp}-{uuid.uuid4().hex[:6]}"
            transcribe.start_transcription_job(
                TranscriptionJobName=job_name,
                Media={'MediaFileUri': f's3://{BUCKET_NAME}/{audio_key}'},
                MediaFormat='webm',
                LanguageCode=TRANSCRIBE_LANGUAGE_CODE,
                OutputBucketName=BUCKET_NAME,
                OutputKey=f"{TRANSCRIBE_OUTPUT_PREFIX.rstrip('/')}/{job_name}/"
            )
            return _json(200, {'jobName': job_name, 'audioKey': audio_key})

        if path == '/api/slideshow/save' and method == 'POST':
            body = json.loads(event.get('body', '{}'))
            story_id = body.get('id')
            if not story_id:
                return _json(400, {'error': 'Missing story id'})
            _save_manifest(story_id, body)
            return _json(200, {'ok': True, 'id': story_id})

        if path == '/api/slideshow/get' and method == 'GET':
            params = event.get('queryStringParameters') or {}
            story_id = params.get('id')
            if not story_id:
                return _json(400, {'error': 'Missing id'})
            return _json(200, _load_manifest(story_id))

        if path == '/api/slideshow/refresh' and method == 'GET':
            params = event.get('queryStringParameters') or {}
            story_id = params.get('id')
            if not story_id:
                return _json(400, {'error': 'Missing id'})
            return _json(200, _refresh_story_manifest(story_id))

        if path == '/api/slideshow/retry-transcription' and method == 'POST':
            body = json.loads(event.get('body', '{}'))
            story_id = body.get('id')
            if not story_id:
                return _json(400, {'error': 'Missing id'})

            manifest = _load_manifest(story_id)
            audio_key = manifest.get('audioKey')
            if not audio_key:
                return _json(400, {'error': 'Missing audio key in manifest'})

            retry_name = f"{TRANSCRIBE_JOB_PREFIX}-retry-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            transcribe.start_transcription_job(
                TranscriptionJobName=retry_name,
                Media={'MediaFileUri': f's3://{BUCKET_NAME}/{audio_key}'},
                MediaFormat='webm',
                LanguageCode=TRANSCRIBE_LANGUAGE_CODE,
                OutputBucketName=BUCKET_NAME,
                OutputKey=f"{TRANSCRIBE_OUTPUT_PREFIX.rstrip('/')}/{retry_name}/"
            )

            manifest['jobName'] = retry_name
            manifest['processingStatus'] = 'transcribing'
            manifest['processingStage'] = 'transcription'
            manifest['processingError'] = ''
            _save_manifest(story_id, manifest)
            return _json(200, {'ok': True, 'jobName': retry_name, 'id': story_id})

        if path == '/api/slideshow/media-url' and method == 'GET':
            params = event.get('queryStringParameters') or {}
            key = params.get('key')
            if not key:
                return _json(400, {'error': 'Missing key'})
            get_url = s3.generate_presigned_url(
                ClientMethod='get_object',
                Params={'Bucket': BUCKET_NAME, 'Key': key},
                ExpiresIn=PRESIGN_TTL_SECONDS
            )
            return _json(200, {'url': get_url, 'key': key})

        if path.startswith('/api/audio/') and method == 'GET':
            key = path.replace('/api/audio/', '', 1)
            obj = s3.get_object(Bucket=BUCKET_NAME, Key=key)
            return {
                'statusCode': 200,
                'headers': {
                    'Content-Type': obj.get('ContentType', 'audio/webm'),
                    'Access-Control-Allow-Origin': ALLOWED_ORIGIN
                },
                'body': base64.b64encode(obj['Body'].read()).decode('utf-8'),
                'isBase64Encoded': True
            }

        if path == '/api/slideshow/delete' and method == 'POST':
            body = json.loads(event.get('body', '{}'))
            story_id = body.get('id')
            if not story_id:
                return _json(400, {'error': 'Missing id'})
            deleted = _delete_story(story_id)
            return _json(200, {'deletedKeys': deleted})

        return _json(404, {'error': 'Not found'})

    except Exception as e:
        return _json(500, {'error': str(e)})
