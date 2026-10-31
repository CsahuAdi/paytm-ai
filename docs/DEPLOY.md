# Deploy ResolveAI (free tiers): Render + MongoDB Atlas

Three free web services (customer service, kirana, librarian) built from the `Dockerfile` by the `render.yaml` Blueprint, on one free Atlas cluster. About 20 minutes of clicking, then 5 to 10 minutes of building. Everything uses the invented demo data. The first boot of each service seeds an empty database; later boots leave the data alone.

Tested here: the exact files the Dockerfile copies, installed into a clean virtualenv from `requirements.txt`, booted with the container start command against an empty database (seeded once, second boot did not reseed, `/health` 200, `/debug/*` 404). **Not tested:** the Docker image build itself (this laptop's Docker cannot pull the base image because of a certificate error) and a real Atlas cluster. Render builds the image on its own servers.

## 1. MongoDB Atlas (free M0)

1. Sign up at https://cloud.mongodb.com and create a project and a cluster: **M0 Free**, provider AWS, any region (Oregon or Virginia sits next to Render's default).
2. **Database Access**: add a user (username + password, "Read and write to any database"). Prefer letters and digits in the password, otherwise URL-encode it in the string.
3. **Network Access**: add `0.0.0.0/0` (Render's free tier has no fixed outbound address).
4. **Connect > Drivers**: copy the connection string, `mongodb+srv://USER:PASSWORD@cluster....mongodb.net/`, and put your password in it. This is your `MONGO_URI`.

## 2. Render

1. Sign up at https://render.com with GitHub (account `CsahuAdi`) and allow access to the `paytm-ai` repository.
2. **Dashboard > New > Environment Group**, name it exactly `resolveai-secrets`, and add these variables (copy the values from your local `.env`):
   - `MONGO_URI` (from step 1)
   - `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `SARVAM_API_KEY`
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`
   - `AGENTMAIL_API_KEY`, `AGENTMAIL_INBOX_ID`, `AGENTMAIL_WEBHOOK_SECRET`
   - **Do not add** `DEMO_CUSTOMER_EMAIL`, `DEMO_CUSTOMER_PHONE` or `DEBUG`: a hosted copy must not hold your real contacts or the reset/fault endpoints.
3. **New > Blueprint**, pick the `paytm-ai` repo, branch `master`, and apply. It creates `resolveai`, `resolveai-kirana` and `resolveai-librarian`. When asked for `PUBLIC_BASE_URL` on `resolveai`, type `https://resolveai.onrender.com` for now.
4. Wait for the three deploys to go live (Logs tab: look for `empty; seeding {...}` then `Uvicorn running`).
5. Open each service's page to read its real URL (Render may add a suffix, such as `resolveai-x1y2.onrender.com`). If `resolveai`'s URL differs from what you typed, edit `PUBLIC_BASE_URL` on that service to the real one and redeploy.

| Service | What to open |
|---|---|
| `resolveai` | `/` (dashboard) and `/static/chat.html` |
| `resolveai-kirana` | `/` (merchant desk) and `/paytm` |
| `resolveai-librarian` | `/` (desk) and `/static/chat.html` |

## 3. Optional: point WhatsApp and email at the hosted copy

Only one URL per webhook, so doing this takes the channel away from the laptop and ngrok.
- Twilio WhatsApp sandbox "When a message comes in": `https://<resolveai url>/webhooks/twilio` (method POST).
- AgentMail webhook: `https://<resolveai url>/webhooks/agentmail`.

## 4. Things that behave differently when hosted

- **Free services sleep after 15 minutes without traffic and take about a minute to wake.** Open all three URLs a couple of minutes before judging or recording.
- The local `data/` folder is wiped on every sleep or redeploy, so the footer's per-model usage meter restarts from zero. It only affects the meter and which models get tried first.
- The hosted copy uses the same model keys as the laptop, so they share the same free quotas (Gemma allows about 1.5 turns a minute across everything).
- No `/debug` endpoints are exposed, so the dashboard-side reset is not available. To reset a hosted database to the seed, run this from the laptop (the `--fake` flag keeps invented contacts; without it your real email and phone would be written into the hosted database):

```
MONGO_URI='mongodb+srv://USER:PASSWORD@cluster....mongodb.net/' .venv/bin/python scripts/reset_demo.py --fake
MONGO_URI='mongodb+srv://USER:PASSWORD@cluster....mongodb.net/' .venv/bin/python scripts/reset_demo.py --pack kirana --fake
MONGO_URI='mongodb+srv://USER:PASSWORD@cluster....mongodb.net/' .venv/bin/python scripts/reset_demo.py --pack librarian --fake
```
- Anyone with a URL can use the dashboard (roles are labels, not login) and the chat pages.
