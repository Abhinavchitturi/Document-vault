# Recording guide: a 30 to 45 second demo

Everything uses fictional sample data. Record the screen at 1080p, browser zoom about 110%,
notifications off.

## Before you record
- Ollama is open, then `./vault start`, then open http://localhost:8000 and sign in as `abhinav`.
- Have `samples/upload-demo/Laptop Warranty.pdf` ready in a Finder window. Do not upload it
  beforehand.
- Ask one question first so the model is warm (the first answer is slower). Then reset the chat.

## Shot list

| Time | Show | Say / caption |
|---|---|---|
| 0-5 s | Dashboard | "My private document vault. AI runs fully on my laptop, nothing goes to the cloud." |
| 5-20 s | **Upload**: drag in `Laptop Warranty.pdf`; it sorts itself as *warranty* | "Drop in a file. It reads it and files it by itself." |
| 20-35 s | **Chat**: type "When does my laptop warranty expire?" -> 11 October 2028. Click the source so the highlighted page opens | "Ask in plain English. The answer comes with the exact page it came from." |
| 35-42 s | Terminal: `ls data/index/files` (unreadable, encrypted names) | "Everything is AES-256 encrypted on disk. The key lives in my Keychain." |
| 42-45 s | End card | "Code on GitHub: link in comments." |

## If you have 10 more seconds
Ask "When does my bike insurance expire?" (14 March 2027) before the upload, or open the
upside-down gas bill scan to show OCR.

## Editing
- Local answers take a few seconds: speed that part up or cut it.
- Caption the video; most people watch on mute.

## Reset between takes
Remove the laptop warranty from **Documents**, or reset everything (this also drops your account,
so you choose the password again):
```bash
./vault stop
rm -rf data && mkdir -p data/index
VAULT_DATABASE_URL=sqlite:///data/index/vault.db python -m backend.upload.ingest samples/pdfs samples/scans --owner abhinav
./vault start
```
