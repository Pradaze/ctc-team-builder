# Putting the dashboard online

## 1. Try it on your computer first

```
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml`: add your Tally key and form ID, put your own email under `admins`, and for now delete the `[auth]` sections and set a password instead (instructions at the bottom of the file). Then:

```
streamlit run app.py
```

Open http://localhost:8501, sign in, go to **Admin** and either **Fetch new applications** or upload the `applicants.csv` you already have (it keeps your CV text). Data is stored in a `data/` folder next to the code.

## 2. Set up sign-in with Microsoft (LBS accounts) or Google

You need an "app registration" so the provider will log people in for you.

**Microsoft:** Azure portal > Microsoft Entra ID > App registrations > New registration. Supported account types: "Accounts in any organizational directory and personal Microsoft accounts". Redirect URI (Web): `https://<your-app>.onrender.com/oauth2callback`, plus `http://localhost:8501/oauth2callback` for testing. Under Certificates & secrets, create a client secret. Copy the Application (client) ID and the secret value into `[auth.microsoft]`. If LBS IT blocks app registrations, use Google instead.

**Google:** Google Cloud console > APIs & Services > Credentials > Create OAuth client ID > Web application. Add the same two redirect URIs. Copy the client ID and secret into `[auth.google]`. On the OAuth consent screen, set the app to "External" and publish it (or add organisers as test users).

Generate `cookie_secret` with `python -c "import secrets; print(secrets.token_hex(32))"`.

Signing in only proves who someone is. Getting in also needs their email (or domain) in the `[access]` lists, so anyone not on the list sees "No access yet".

## 3. Deploy on Render

1. Put this folder in a **private** GitHub repository. `.gitignore` already keeps out secrets and data.
2. In Render: New > Blueprint > choose the repository. It reads `render.yaml`: a Starter web service in Frankfurt with a 1 GB disk mounted at `/var/data`, where the data lives.
3. In the service's Environment tab, add a **Secret File** named `secrets.toml` with the contents of your secrets file, with `redirect_uri` set to `https://<your-app>.onrender.com/oauth2callback`. The app reads it from `/etc/secrets/secrets.toml`.
4. Deploy, open the URL, sign in, and upload or fetch the data from **Admin**.

To change who has access, edit the Secret File and restart the service.

## 4. Data protection

The site stores applicants' personal details and CV text on Render's servers (EU region). Check this fits LBS's data-protection rules for the competition, keep the access list to organisers, and delete the data (Admin > download a copy if needed, then delete the disk or the service) once the competition no longer needs it. The activity log on the Admin page records who changed what.
