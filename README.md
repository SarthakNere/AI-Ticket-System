# AI Ticket Support System

A Flask-based support portal for managing customer issues, triaging requests, and enabling AI-assisted ticket handling. The application is designed around a clear split between customer and analyzer experiences:

- Customers can sign up, sign in, submit tickets, track their own ticket history, and edit unresolved requests.
- Analysts can sign in to a support dashboard, review the ticket queue, update priorities/status, write replies, and monitor support analytics.
- Optional AI features use Groq to classify ticket issues and generate draft replies based on subject and description.

The project stores user accounts and ticket data in Supabase Auth + Postgres, while Flask manages server-side session state.

## Features

### Customer portal
- Secure customer sign-up and sign-in with Supabase Auth
- Role-based access control for customer accounts
- Submit support tickets with subject and description
- View personal ticket history and ticket detail pages
- Edit unresolved tickets before they are marked resolved
- Receive notifications when an analyzer replies

### Analyzer portal
- Analyzer sign-in to a dedicated support dashboard
- Full queue view of submitted tickets
- Search, filter, and sort by ticket status and priority
- Update ticket status and priority
- Review submitted descriptions and AI-generated metadata
- Send direct customer replies from the ticket detail screen
- Access support analytics for ticket trends

### AI-powered support workflow
- Optional Groq integration for:
  - ticket classification (technical, billing, account, general)
  - priority assignment (low, medium, high, urgent)
  - sentiment detection
  - suggested reply drafts for support agents
- Prompt-safety checks are included to reduce unsafe or malformed AI prompting behavior

### Security and session handling
- Server-side Flask sessions stored locally under `instance/flask_sessions`
- CSRF protection for POST requests
- Role-based routing and permission checks
- Supabase Row Level Security policies enforce customer/analyzer data separation

## Tech Stack

- Python 3.10+
- Flask 3.1+
- Flask-Session
- Supabase Python client
- PostgreSQL via Supabase
- Groq API for AI analysis and reply drafting
- Jinja2 templates with Bootstrap styling
- Chart.js for analytics visualization

## Project Structure

```text
AI Ticket Support System/
├── app.py                     # Main Flask application
├── ai_service.py              # Groq AI ticket analysis and reply generation
├── init_db.py                 # Local DB bootstrap utility (if used in older workflows)
├── requirements.txt           # Python dependencies
├── .env.example               # Example environment variables
├── README_SETUP.txt           # Supabase setup notes
├── supabase_schema.sql        # Database schema and RLS policies
├── instance/                  # Local runtime files and session storage
├── static/                    # CSS, JS, and static assets
├── templates/                 # Jinja templates for UI screens
├── .env                       # Your local environment variables (not committed)
└── tickets.db                 # Legacy local SQLite DB (left untouched by the Supabase app)
```

## Prerequisites

Before running the app, you will need:

- A Supabase project
- A Supabase anon/public key
- A working Python environment
- A Groq API key (optional but recommended for AI features)
- Access to the app base URL used by your browser (for local development this is usually `http://127.0.0.1:5000`)

## Setup Instructions

### 1) Create the Supabase project

1. Go to the Supabase dashboard.
2. Create a new project.
3. Open Project Settings > API.
4. Copy:
   - Project URL
   - Publishable/anon key

Important: Do not use a service-role or secret key in the browser or as the app's anon key.

### 2) Apply the database schema

Open the SQL editor in Supabase and run the contents of `supabase_schema.sql`.

This script creates:
- `profiles` table
- `tickets` table
- a trigger for automatic customer profile creation
- indexes for support queries
- row-level security policies for customers and analyzers

### 3) Create analyzer accounts

Support team members must be created in Supabase Auth and assigned the `analyzer` role in `public.profiles`.

Example workflow:
- Create the support user in Supabase Authentication
- Update their role in the `profiles` table to `analyzer`

Do not expose a public sign-up form for analyzer roles.

### 4) Configure local environment variables

Copy `.env.example` to a new `.env` file and update the values:

```env
FLASK_SECRET_KEY=generate-a-long-random-secret
SESSION_COOKIE_SECURE=false
FLASK_DEBUG=true

SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_ANON_KEY=your-supabase-publishable-or-anon-key
APP_BASE_URL=http://127.0.0.1:5000

GROQ_API_KEY=your-groq-api-key
```

Notes:
- `SUPABASE_URL` should be the project base URL, not a full `/rest/v1` or `/auth/v1` endpoint.
- `APP_BASE_URL` must match the exact local or deployed address used in the browser.
- Set `SESSION_COOKIE_SECURE=true` when serving the app over HTTPS in production.

### 5) Install dependencies

```bash
python -m venv venv
# Windows
.\venv\Scripts\Activate.ps1

# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 6) Run the application

```bash
python app.py
```

Then open the local address printed in the terminal, typically:

```text
http://127.0.0.1:5000
```

## Typical User Flow

### Customer flow
1. Create a customer account
2. Confirm email if required by Supabase
3. Sign in
4. Submit a support ticket
5. Review ticket status and AI-generated category/priority metadata
6. Receive and read analyzer replies from the customer dashboard

### Analyzer flow
1. Sign in to the analyzer portal
2. Review all tickets in the queue
3. Filter by status or priority
4. Update issue handling details
5. Draft and send a response to the customer
6. Review trends in the analytics dashboard

## AI Analysis Behavior

When Groq is configured, the app can analyze ticket content and assign values such as:

- `category`: Technical, Billing, Account, or General
- `priority`: Low, Medium, High, Urgent
- `sentiment`: Positive, Neutral, Negative

It also generates a suggested reply for support staff, which they can review and edit before sending.

If AI processing fails or the ticket content looks malicious, the app continues to save the ticket but skips or warns on AI processing instead of blocking the customer experience.

## Deployment Notes

- Run behind HTTPS in production
- Set `SESSION_COOKIE_SECURE=true`
- Set a strong `FLASK_SECRET_KEY`
- Disable debug mode with `FLASK_DEBUG=false`
- Keep your `.env` file outside of source control
- Use a shared server-side session backend for multi-instance deployments if needed

## Security Considerations

This project is designed for secure customer support workflows, but production deployments should still review:

- Supabase RLS policies
- secret management and environment isolation
- rate limiting for public-facing forms
- secure hosting and TLS configuration
- access control for analyzer accounts

## Notes

- The project uses Supabase as the system of record for accounts and tickets.
- Existing local SQLite ticket data is not migrated into Supabase and should be treated as legacy data.
- Bootstrap, Bootstrap Icons, Chart.js, and Google Fonts rely on CDN access from the browser.

## License

This project does not currently include a project license file. Before production or public distribution, add an appropriate license that matches your intended usage and legal requirements.
