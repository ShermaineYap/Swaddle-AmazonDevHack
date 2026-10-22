# Finish and submit Swaddle (about 30 minutes)

Deadline: **Sat 24 Oct, 03:00 Malaysia time** (Oct 23, 12:00 PDT). Aim to submit Friday evening.

## 1. Put the code on GitHub (10 min)
1. Go to https://github.com/new. Name the repo `swaddle`, choose **Public**, and **don't** add a README or license (they're already in the code). Click Create.
2. Open Terminal and run:
   ```bash
   cd "~/Documents/icloud doc/Hacks/Amazon Dev Hack/swaddle"
   git remote add origin https://github.com/<YOUR-USERNAME>/swaddle.git
   git push -u origin main
   ```
3. On the repo page, click the gear next to **About** and make sure the license shows **MIT**. GitHub detects it automatically from the LICENSE file.

## 2. Upload the video (10 min)
1. Go to https://studio.youtube.com, then Create → Upload, and pick `swaddle_demo.mp4`.
2. Title: `Swaddle: hands-free baby care for Alexa+ (Build, Ship, Shape hackathon)`
3. Visibility: **Public**. Unlisted doesn't meet the rules.
4. Copy the link and open it once in a private window to check that it plays while logged out.

## 3. Fill in the Devpost form (10 min)
Open `docs/SUBMISSION.md` and copy each section into the matching field.
- Replace `<GITHUB URL>`, `<YOUTUBE URL>` and `<YOUR GITHUB USERNAME>` everywhere, including the link at the top of `README.md`. Commit and push that README change too.
- Track: **Alexa+**. Mini challenge: **Open Source**.
- Paste the **Product feedback** blocks (required) and the **Friction log** (optional, worth up to a 10% bonus).
- Add `docs/screenshot-report.png` and `docs/screenshot-fever.png` to the image gallery.
- Click **Submit**, not just Save, and look for the confirmation email.

## Optional: a live server for judges
The FAQ says a runnable repo plus the video is enough. If you want a live URL anyway: on https://render.com, choose New → Blueprint → select the repo. It reads `render.yaml` and deploys for free. Add the URL under "Try it out".

## If you win
Non-US winners must return a W-8BEN tax form and an affidavit within 10 business days, so keep your passport handy in early December.
