# AWS Setup and Deployment

This guide explains how to deploy SilverStory Studio with:

- S3 static hosting (frontend)
- Lambda Function URL (backend API)
- Optional CloudFront + custom domain + TLS

## 1. Prerequisites

- AWS account with permissions for S3, Lambda, IAM, CloudFront, ACM, Route 53 (optional)
- AWS CLI configured (`aws configure`)
- Python 3.10+ locally

## 2. Create S3 Bucket

Create one bucket to store both:

- Frontend static files
- Story media, audio, and manifests

Example (replace names/region):

```bash
aws s3api create-bucket --bucket YOUR_BUCKET_NAME --region us-east-1
```

### S3 CORS

Apply CORS so browser uploads/downloads work:

```json
[
  {
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET", "PUT", "POST", "HEAD"],
    "AllowedOrigins": ["*"],
    "ExposeHeaders": []
  }
]
```

Apply:

```bash
aws s3api put-bucket-cors \
  --bucket YOUR_BUCKET_NAME \
  --cors-configuration file://cors.json
```

## 3. Create IAM Role for Lambda

Create a Lambda execution role with at least:

- CloudWatch logs permissions
- S3 read/write permissions for your app bucket
- Amazon Transcribe permissions:
  - `transcribe:StartTranscriptionJob`
  - `transcribe:GetTranscriptionJob`

Scope S3 permissions to your bucket paths where possible.

## 4. Create Lambda Function

Runtime: Python 3.12
Handler: `lambda_function.lambda_handler`

Deploy code:

```bash
cd backend
zip -j lambda-function.zip lambda_function.py
aws lambda create-function \
  --function-name silverstory-backend \
  --runtime python3.12 \
  --handler lambda_function.lambda_handler \
  --role arn:aws:iam::YOUR_ACCOUNT_ID:role/YOUR_LAMBDA_ROLE \
  --zip-file fileb://lambda-function.zip
```

### Lambda environment variables

Set:

- `APP_BUCKET=YOUR_BUCKET_NAME`
- `AWS_REGION=us-east-1`
- `TRANSCRIBE_LANGUAGE_CODE=en-US`
- `TRANSCRIBE_JOB_PREFIX=storyscribe`
- `TRANSCRIBE_OUTPUT_PREFIX=transcribe`
- `PRESIGN_TTL_SECONDS=3600`
- `ALLOWED_ORIGIN=*` (or your exact domain)

Example:

```bash
aws lambda update-function-configuration \
  --function-name silverstory-backend \
  --environment "Variables={APP_BUCKET=YOUR_BUCKET_NAME,AWS_REGION=us-east-1,TRANSCRIBE_LANGUAGE_CODE=en-US,TRANSCRIBE_JOB_PREFIX=storyscribe,TRANSCRIBE_OUTPUT_PREFIX=transcribe,PRESIGN_TTL_SECONDS=3600,ALLOWED_ORIGIN=*}"
```

## 5. Enable Lambda Function URL

Create function URL with CORS enabled.

Example:

```bash
aws lambda create-function-url-config \
  --function-name silverstory-backend \
  --auth-type NONE \
  --cors 'AllowOrigins=["*"],AllowMethods=["GET","POST","PUT","OPTIONS"],AllowHeaders=["*"],MaxAge=86400'
```

Copy the resulting Function URL.

## 6. Configure Frontend

Copy config template:

```bash
cp web/config.example.js web/config.js
```

Edit `web/config.js`:

```js
window.APP_CONFIG = {
  apiBase: 'https://YOUR_FUNCTION_URL',
  appPassword: 'choose-a-strong-password'
};
```

## 7. Deploy Frontend to S3

Upload frontend files:

```bash
aws s3 cp web/ s3://YOUR_BUCKET_NAME/ --recursive --exclude "config.example.js"
```

If hosting from same bucket, the app will be at `https://YOUR_BUCKET_NAME.s3.amazonaws.com/index.html`.

## 8. Optional: CloudFront + Custom Domain

Recommended for production.

1. Create CloudFront distribution with S3 origin.
2. Set default root object to `index.html`.
3. Add custom domain (CNAME) to distribution.
4. Request ACM certificate in `us-east-1` for domain.
5. Attach certificate to CloudFront.
6. Create Route 53 alias record to CloudFront.
7. Update Lambda `ALLOWED_ORIGIN` to your site domain.

## 9. Updating Backend

```bash
cd backend
zip -j lambda-function.zip lambda_function.py
aws lambda update-function-code \
  --function-name silverstory-backend \
  --zip-file fileb://lambda-function.zip
```

## 10. Updating Frontend

```bash
aws s3 cp web/ s3://YOUR_BUCKET_NAME/ --recursive --exclude "config.example.js"
```

## 11. API Endpoints (Backend)

- `POST /api/slideshow/create-upload-url`
- `POST /api/upload`
- `POST /api/slideshow/save`
- `GET /api/slideshow/get?id=...`
- `GET /api/slideshow/refresh?id=...`
- `POST /api/slideshow/retry-transcription`
- `GET /api/slideshow/media-url?key=...`
- `GET /api/audio/{key}`
- `POST /api/slideshow/delete`

## 12. Security Recommendations

- Restrict CORS to your domain (not `*`) in production.
- Keep `web/config.js` out of source control (`.gitignore` already set).
- Use least-privilege IAM policies for Lambda.
- Add CloudFront/WAF if exposing publicly.
