# Setup

Getting an Instagram API token is the hard part. The rest takes two minutes.

Budget about 15 minutes for the first account, 2 minutes for each one after.

## Before you start: can you actually use this?

**You need an Instagram Professional account (Business or Creator).** Personal accounts cannot
access the API at all. This is Meta's rule, not something this project can work around.

To check or change it: Instagram app → Settings → Account type and tools. Switching to Creator
is free, reversible, and takes about 30 seconds.

You also need a Facebook account to log in to the developer site. You do **not** need a Facebook
Page, and you do not need to connect one.

---

## Step 1: Create a Meta app

1. Go to [developers.facebook.com/apps](https://developers.facebook.com/apps) and log in.
2. Click **Create App**.
3. For "What do you want your app to do?", choose **Other**, then **Business** as the app type.
4. Give it any name. It is only visible to you. Create the app.

You do not need to submit anything for review. Review is only required when other people's
accounts use your app. You are only using your own.

---

## Step 2: Add Instagram to the app

1. In the left sidebar of your new app, find **Instagram** and click **Set up**.
2. You will see two options. Choose **API setup with Instagram login**.

**This choice matters.** Pick the Instagram Login option, not the Facebook Login one.

You may see a banner suggesting insights require Facebook Login. That banner refers to hashtag
and account-level insights. The per-reel media insights this project needs, including
`ig_reels_avg_watch_time`, work correctly on Instagram Login. Choosing Facebook Login instead
means connecting a Page and a Business account for no benefit.

---

## Step 3: Generate a token

Still in **API setup with Instagram login**:

1. Find **Generate access tokens**.
2. Click **Add account** and log in to the Instagram account you want to read.
3. Approve the permissions. The scope you need is `instagram_business_basic`.
4. Your account appears with a **Generate token** button. Click it.
5. Copy the token. It starts with `IGAA` and is long, roughly 150 to 200 characters.

Copy it now. The dashboard will not show it again, though you can always generate a new one.

Repeat this step for each account you want to track.

---

## Step 4: Put it in .env

```bash
cp .env.example .env
```

Open `.env` and paste your token. Pick any short name you like for the account:

```
IG_ACCOUNT_MAIN_TOKEN=IGAAxxxxxxxxxxxxxxxxxx
IG_ACCOUNT_MAIN_ID=
```

Leave the ID blank for now. The next step fills it in for you.

The name between `IG_ACCOUNT_` and `_TOKEN` is yours to choose. Letters and digits only, no
underscores. It becomes the name you use when asking questions, so `IG_ACCOUNT_MAIN_*` lets
you say "how did my main account do".

---

## Step 5: Run the checker

```bash
python check_setup.py
```

It verifies your dependencies, tests the token against Instagram, tells you which account the
token belongs to, and prints the account ID for you to paste in:

```
[  OK  ] mcp package installed
[  OK  ] ffprobe found
[  OK  ] .env found

found 1 account block(s): main

[  OK  ] main: token works, account @yourhandle
[ WARN ] main: IG_ACCOUNT_MAIN_ID is empty. Paste this into .env:

         IG_ACCOUNT_MAIN_ID=17841400000000000
```

Paste that line into `.env`, run the checker again, and you should see all green.

Then confirm it pulls real data:

```bash
python server.py --selftest
```

---

## Step 6: Connect it to Claude

```bash
claude mcp add ig-analytics -- /absolute/path/.venv/bin/python /absolute/path/server.py
```

Use absolute paths. Then ask: *"How did my last 10 reels do?"*

Your tokens stay in `.env`. Nothing sensitive goes into the MCP config.

---

## Keeping it working

Instagram tokens expire after about **60 days**.

**An expired token cannot be refreshed.** Meta will not renew a dead one, so once it lapses your
only option is Step 3 again. The fix is to refresh while it is still alive:

```bash
python refresh_tokens.py --if-older-than 7
```

Each refresh resets the full 60 days, so refreshing early costs nothing. Run it weekly.

On a server, cron works:

```
0 7 * * 1 /path/.venv/bin/python /path/refresh_tokens.py --env-file /path/.env
```

On macOS, be careful: a `launchd` job cannot read files in Desktop, Documents or Downloads
unless you grant Full Disk Access. It fails with `Operation not permitted` and looks healthy
while doing nothing. Either keep the project outside those folders, grant the permission, or
run the refresh from a shell hook instead.

Refreshing does not invalidate the old token, so several machines can each refresh their own
copy of `.env`. Nothing needs syncing between them.

---

## Troubleshooting

**"token rejected -- Error validating access token"**
Expired or invalidated. A password change or a Meta security reset also kills tokens. Redo Step 3.

**Completion rate comes back empty**
`ffprobe` is missing, so duration cannot be measured. Install ffmpeg.

**"Unsupported get request" or empty insights**
Usually the account is still Personal rather than Professional, or the reel is too new for
Instagram to have computed insights yet. Wait a few hours and retry.

**Insights work but `ig_reels_avg_watch_time` is missing**
That metric only exists for reels. Photos and carousels will not have it.

**Duplicate keys in .env**
If the same key appears twice, which one wins depends on the loader. Check for duplicates before
debugging anything else. `refresh_tokens.py` rewrites every occurrence for this reason.
