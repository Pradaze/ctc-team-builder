# Putting the dashboard online for free

The free setup has two parts:

- **Neon** keeps the data (applications, reviews, teams, activity log) in a free Postgres database. Free, no card, 1 GB of storage per project. It sleeps after 5 minutes unused and wakes in well under a second.
- **Streamlit Community Cloud** runs the app. Free, deploys from your GitHub repository, and sleeps when nobody has used it for a while; the first visitor after that clicks a button to wake it and waits a minute or so.

On your own computer nothing changes: without a `DATABASE_URL` the app keeps using the `data/` folder.

## 1. Create the database (Neon)

1. Sign up at neon.tech.
2. Create a project. Pick a region in Europe (Frankfurt or London) to keep the data in Europe.
3. On the project dashboard, click **Connect** and copy the connection string. It starts with `postgresql://` and ends with `?sslmode=require`.

The app creates its own tables the first time it connects.

## 2. Set up sign-in (Microsoft or Google)

You need an app registration so the provider can sign people in for you. You'll know your app's address after step 3; you can create the registration now and add the address afterwards.

**Microsoft:** Azure portal > Microsoft Entra ID > App registrations > New registration. Account types: "Accounts in any organizational directory and personal Microsoft accounts". Redirect URIs (Web): `https://<your-app>.streamlit.app/oauth2callback` and `http://localhost:8501/oauth2callback`. Under Certificates & secrets, create a client secret. Copy the client ID and the secret's value. If LBS IT blocks app registrations, use Google.

**Google:** Google Cloud console > APIs & Services > Credentials > Create OAuth client ID > Web application, with the same two redirect URIs. On the OAuth consent screen choose External and publish the app (or add each organiser as a test user).

Signing in only proves who someone is. To get in, their email or domain must also be in the `[access]` lists.

## 3. Deploy (Streamlit Community Cloud)

1. Go to share.streamlit.io and sign in with GitHub. Allow it access to your private `ctc-team-builder` repository when asked.
2. Click **Create app** > deploy from GitHub. Repository: `ctc-team-builder`, branch `main`, main file `app.py`. Choose a custom subdomain, e.g. `ctc-teams`, which gives `https://ctc-teams.streamlit.app`.
3. Open **Advanced settings**. Set Python to 3.12. In **Secrets**, paste your secrets file (start from `.streamlit/secrets.toml.example`) with:
   - `TALLY_API_KEY` and `TALLY_FORM_ID`
   - `DATABASE_URL` = the Neon connection string
   - `[access]` lists (admins, reviewers, viewers)
   - `[auth]` with `redirect_uri = "https://ctc-teams.streamlit.app/oauth2callback"`, a `cookie_secret` (`python -c "import secrets; print(secrets.token_hex(32))"`), and your provider's client ID and secret. Delete the provider section you don't use. No password lines.
4. Click **Deploy**. The first build takes a few minutes.
5. Open the app, sign in, go to **Admin** and upload your `applicants.csv` (it keeps your CV text and review marks), or fetch from Tally.

To change who has access later: app menu > **Settings** > **Secrets**, edit the `[access]` lists, save. The app restarts with the new list.

Every `git push` to `main` updates the app automatically. Data in Neon isn't affected by redeploys.

## Using the same data on your computer

Add the same `DATABASE_URL` line to your local `.streamlit/secrets.toml`. Your local copy and the website then read and write the same data. Remove it to go back to the `data/` folder.

## Alternative host: Render's free plan

`render.yaml` sets up a free Render service in Frankfurt that also uses Neon for data. It works, but free Render instances have very little processing power (0.1 CPU), so building features and teams is noticeably slower, and it sleeps after 15 minutes. If you use it: New > Blueprint > choose the repository, add a Secret File named `secrets.toml` with the same content as above (with `redirect_uri` set to your `onrender.com` address), and deploy.

## Data protection

Applicants' details and CV text will sit in Neon (in the region you chose) and pass through Streamlit's servers, which are outside the EU. Check this is acceptable under LBS's data-protection rules for the competition, keep the access list to organisers, and delete the Neon project once the data is no longer needed (Admin > download a copy first if you need one).
