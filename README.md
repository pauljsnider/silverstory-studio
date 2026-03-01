# SilverStory Studio

SilverStory Studio is a simple web app to create narrated photo/video stories:

- Upload photos and videos
- Reorder slides
- Record one narration track
- Preview the full story
- Publish and share a single link
- Re-open stories from history and edit them

This repository is intentionally focused on the slideshow workflow only.

## Project Structure

- `web/index.html` - Frontend application (single-page app)
- `web/config.example.js` - Frontend config template
- `backend/lambda_function.py` - Serverless backend for uploads, manifests, and transcription status
- `docs/AWS_SETUP.md` - Full AWS setup and deployment guide
- `docs/ALTERNATIVES.md` - Cost/free alternatives and tradeoffs for older-user workflows

## Quick Start

1. Read `docs/AWS_SETUP.md` and deploy backend + frontend.
2. Copy `web/config.example.js` to `web/config.js` and fill in your values.
3. Upload `web/` to your static hosting bucket.
4. Open the site and create your first short test story.

## Frontend Configuration

`web/config.js`:

```js
window.APP_CONFIG = {
  apiBase: 'https://YOUR_BACKEND_URL',
  appPassword: 'change-me'
};
```

- `apiBase` should point to your Lambda Function URL or API Gateway base URL.
- `appPassword` is the shared password required for create/edit mode.

## Backend Environment Variables

Set these on your Lambda function:

- `APP_BUCKET` (required)
- `AWS_REGION` (optional, default `us-east-1`)
- `TRANSCRIBE_LANGUAGE_CODE` (optional, default `en-US`)
- `TRANSCRIBE_JOB_PREFIX` (optional, default `storyscribe`)
- `TRANSCRIBE_OUTPUT_PREFIX` (optional, default `transcribe`)
- `PRESIGN_TTL_SECONDS` (optional, default `3600`)
- `ALLOWED_ORIGIN` (optional, default `*`)

## Notes

- For reliability, keep initial test stories short.
- For longer sessions, run a short validation story first (a few images + one short video).
- See `docs/ALTERNATIVES.md` for mainstream free/freemium tools and cost model notes.
