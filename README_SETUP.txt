SUPPORTDESK - SUPABASE SETUP

WHAT THE APP USES
- Supabase Auth for customer and analyzer sign-in
- Supabase Postgres for profiles and tickets
- Row Level Security: customers can read and edit their own unresolved tickets; analyzers can read and update the support queue
- Flask server-side sessions for login tokens
- Groq for optional AI ticket analysis and reply drafts

1. CREATE A SUPABASE PROJECT
- Create a project at https://supabase.com/dashboard.
- In Project Settings > API, copy the Project URL and the publishable key (or legacy anon key).
- Never put a Supabase service-role/secret key in this app or in a browser.

2. CREATE THE DATABASE TABLES AND ACCESS POLICIES
- In the Supabase project, open SQL Editor.
- Open supabase_schema.sql in this project, copy its contents into a new SQL query, and run it.
- The script creates the profiles and tickets tables, account-profile trigger, indexes, and row-level security policies.
- If the database already exists, rerun the script to add the customer-facing reply and notification fields.
- If replies still fail with PGRST204, confirm analyzer_reply, analyzer_reply_at, and customer_reply_seen_at exist on public.tickets, then run NOTIFY pgrst, 'reload schema'; in the SQL Editor.
- New signups always receive the customer role. Customers cannot assign themselves the analyzer role.

3. CREATE ANALYZER ACCOUNTS
- In Supabase Authentication > Users, invite or create the support-team user's account.
- After the user exists, run this in Supabase SQL Editor, replacing the email:

  update public.profiles
  set role = 'analyzer'
  where id = (
    select id from auth.users where email = 'agent@example.com'
  );

- Repeat for each trusted analyzer. Do not add a public analyzer sign-up form.
- In Supabase Authentication > URL Configuration, set Site URL to your APP_BASE_URL and add APP_BASE_URL/auth/confirmed to Redirect URLs. The protocol, hostname, and port must match what you use in the browser (127.0.0.1 and localhost are different).
- The app redirects confirmation emails to APP_BASE_URL/auth/confirmed. If a confirmation link was already sent with an incorrect destination, use Resend confirmation email on the customer sign-in page after updating the redirect settings.
- Configure Supabase email confirmation and SMTP under Authentication settings if customers should confirm email addresses. Until configured, Supabase's default email settings may limit delivery.

4. CONFIGURE THE LOCAL ENVIRONMENT
- Create your local .env file using .env.example as a guide. Do not overwrite your existing .env; add the new variables to it.
- Set SUPABASE_URL to the project URL (the app also removes a trailing /rest/v1 or /auth/v1 path if you copied an API endpoint).
- Set SUPABASE_ANON_KEY to the publishable key or legacy anon key.
- Set APP_BASE_URL to the exact base address customers use to open the app. For local development this is usually http://127.0.0.1:5000.
- Set FLASK_SECRET_KEY to a newly generated random secret. Keep it private.
- Generate one with: python -c "import secrets; print(secrets.token_hex(32))"
- Set GROQ_API_KEY if you want AI analysis and reply drafts; AI remains optional.
- Keep SESSION_COOKIE_SECURE=false only for local HTTP development. Set it to true when hosted over HTTPS.
- Never use a Supabase service-role/secret key as SUPABASE_ANON_KEY. Keep all real credentials out of source control and chat.

5. INSTALL AND RUN
- In PowerShell, activate your virtual environment and install dependencies:

  .\venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  python app.py

- Open the local address printed in the terminal.
- Customers use Customer portal to sign up, sign in, submit tickets, see their own ticket history, and edit their own unresolved requests.
- Analyzers use Analyzer portal to sign in, view and update tickets, and see analytics.

DATA AND DEPLOYMENT NOTES
- New ticket records and Supabase Auth users are stored in Supabase. Existing local SQLite ticket records are discarded from the app and are not migrated.
- The existing instance/tickets.db file is left untouched; it can be archived or removed manually after you confirm it is no longer needed.
- Flask's login sessions are server-side files under instance/flask_sessions, not ticket records. For a multi-instance deployment, configure a shared server-side session backend such as Redis.
- Deploy behind HTTPS, set SESSION_COOKIE_SECURE=true, set a strong FLASK_SECRET_KEY, and set FLASK_DEBUG=false.
- AI reply drafts are not sent automatically. An analyzer can review, edit, and post a reply to the customer's ticket; the customer sees an in-app notification on their next visit to My tickets. This does not send email or push notifications.
- Bootstrap, Bootstrap Icons, Chart.js, and Google Fonts load from CDNs and require an internet connection.
