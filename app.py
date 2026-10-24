from datetime import datetime, timezone
from functools import wraps
import hmac
import logging
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from flask_session import Session
from supabase import create_client

from ai_service import PromptAttackDetected, analyze_ticket, generate_suggested_reply

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or os.urandom(32)
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = str(Path(app.instance_path) / "flask_sessions")
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
Path(app.config["SESSION_FILE_DIR"]).mkdir(parents=True, exist_ok=True)
Session(app)

def normalize_supabase_url(value):
    parsed = urlsplit(value.strip())
    path = parsed.path.rstrip("/")
    for api_path in ("/rest/v1", "/auth/v1"):
        if path.endswith(api_path):
            path = path[:-len(api_path)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


SUPABASE_URL = normalize_supabase_url(os.getenv("SUPABASE_URL", ""))
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000").strip().rstrip("/")
EMAIL_CONFIRMATION_URL = f"{APP_BASE_URL}/auth/confirmed"

logging.getLogger("httpx").setLevel(logging.WARNING)


@app.context_processor
def inject_supabase_status():
    return {
        "supabase_ready": bool(SUPABASE_URL and SUPABASE_KEY),
        "csrf_token": lambda: session.get("csrf_token", ""),
    }


@app.before_request
def protect_post_requests():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not hmac.compare_digest(submitted_token, session["csrf_token"]):
            abort(400, description="Invalid or missing CSRF token.")


def get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY."
        )
    if "supabase" not in g:
        g.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return g.supabase


def clear_auth_session():
    csrf_token = session.get("csrf_token")
    session.clear()
    if csrf_token:
        session["csrf_token"] = csrf_token


def auth_failure_message(action, error):
    code = getattr(error, "code", None)
    known_errors = {
        "user_already_exists": "An account already exists for this email. Try signing in instead.",
        "email_exists": "An account already exists for this email. Try signing in instead.",
        "invalid_credentials": "The email or password is incorrect.",
        "email_not_confirmed": "Confirm your email address before signing in.",
        "signup_disabled": "Customer sign-up is disabled in Supabase Auth settings.",
        "weak_password": "Choose a stronger password that meets the Supabase password requirements.",
        "email_address_invalid": "Check the email address and try again.",
    }
    if isinstance(code, str) and code in known_errors:
        return known_errors[code]
    if isinstance(code, str) and code.replace("_", "").replace("-", "").isalnum():
        return (
            f"Supabase could not {action} (error code: {code}). "
            "Check the Flask terminal for details."
        )
    status_code = getattr(error, "status_code", None)
    if status_code is not None:
        return (
            f"Supabase could not {action} (HTTP {status_code}). "
            "Check the Flask terminal for details."
        )
    return f"We could not {action}. Check the Flask terminal for details."


def authenticated_user():
    access_token = session.get("access_token")
    if not access_token:
        return None

    client = get_supabase()
    try:
        auth_result = client.auth.get_user(access_token)
    except Exception:
        refresh_token = session.get("refresh_token")
        if not refresh_token:
            clear_auth_session()
            return None
        try:
            refreshed = client.auth.refresh_session(refresh_token)
            if not refreshed.session:
                clear_auth_session()
                return None
            access_token = refreshed.session.access_token
            session["access_token"] = access_token
            session["refresh_token"] = refreshed.session.refresh_token
            auth_result = refreshed
        except Exception:
            app.logger.exception("Supabase session refresh failed")
            raise

    user = getattr(auth_result, "user", None)
    if user is None:
        clear_auth_session()
        return None

    client.postgrest.auth(access_token)
    profile_result = (
        client.table("profiles")
        .select("id, full_name, role")
        .eq("id", str(user.id))
        .limit(1)
        .execute()
    )
    profiles = profile_result.data or []
    if not profiles:
        clear_auth_session()
        return None

    profile = profiles[0]
    g.supabase = client
    g.user = {
        "id": str(user.id),
        "email": user.email or "",
        "full_name": profile.get("full_name") or user.email or "Customer",
        "role": profile["role"],
    }
    return g.user


def role_required(role):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            try:
                user = authenticated_user()
            except Exception:
                app.logger.exception("Could not verify the Supabase login")
                flash("We could not verify your session. Please try again.", "danger")
                return redirect(url_for(f"{role}_login"))

            if user is None:
                flash("Please sign in to continue.", "warning")
                return redirect(url_for(f"{role}_login"))
            if user["role"] != role:
                flash("That page is not available for this account.", "warning")
                destination = (
                    "analyzer_dashboard"
                    if user["role"] == "analyzer"
                    else "customer_dashboard"
                )
                return redirect(url_for(destination))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def log_ai_failure(stage, error):
    app.logger.warning("AI %s unavailable: %s", stage, error)
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        status_code = getattr(error, "code", None)

    if isinstance(error, PromptAttackDetected):
        flash(
            "The ticket was saved, but AI processing was skipped because "
            "the ticket text was flagged as a prompt attack.",
            "warning",
        )
    elif status_code == 429:
        flash(
            "The ticket was saved, but the Groq API quota or rate limit "
            "was reached. Review your Groq project quota and billing.",
            "warning",
        )
    else:
        flash(
            "The ticket was saved, but AI processing failed. "
            "Check the application logs for details.",
            "warning",
        )


def fetch_ticket(ticket_id):
    result = (
        g.supabase.table("tickets")
        .select("*")
        .eq("id", ticket_id)
        .limit(1)
        .execute()
    )
    tickets = result.data or []
    return format_ticket(tickets[0]) if tickets else None


def format_ticket(ticket):
    ticket = dict(ticket)
    reply_at = ticket.get("analyzer_reply_at")
    seen_at = ticket.get("customer_reply_seen_at")
    ticket["has_unread_reply"] = False
    if reply_at:
        try:
            parsed_reply_at = datetime.fromisoformat(reply_at.replace("Z", "+00:00"))
            parsed_seen_at = (
                datetime.fromisoformat(seen_at.replace("Z", "+00:00"))
                if seen_at
                else None
            )
            ticket["has_unread_reply"] = (
                parsed_seen_at is None or parsed_reply_at > parsed_seen_at
            )
        except (AttributeError, TypeError, ValueError):
            ticket["has_unread_reply"] = False

    created_at = ticket.get("created_at")
    if created_at:
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            ticket["created_display"] = parsed.strftime("%b %d, %Y")
            ticket["created_long_display"] = parsed.strftime("%B %d, %Y at %I:%M %p")
        except (AttributeError, ValueError):
            ticket["created_display"] = "Date unavailable"
            ticket["created_long_display"] = "date unavailable"
    else:
        ticket["created_display"] = "Date unavailable"
        ticket["created_long_display"] = "date unavailable"
    return ticket


def tickets_for_dashboard(analyzer=False):
    query = g.supabase.table("tickets").select("*")
    if not analyzer:
        query = query.eq("customer_id", g.user["id"])
    status_filter = request.args.get("status", "").strip()
    priority_filter = request.args.get("priority", "").strip()
    search = request.args.get("search", "").strip()[:200]

    rows = query.order("created_at", desc=True).execute().data or []
    counts = {
        "total": len(rows),
        "open": sum(row.get("status") == "Open" for row in rows),
        "in_progress": sum(row.get("status") == "In Progress" for row in rows),
        "resolved": sum(row.get("status") == "Resolved" for row in rows),
    }
    if status_filter in ["Open", "In Progress", "Resolved"]:
        rows = [row for row in rows if row.get("status") == status_filter]
    else:
        status_filter = ""
    if priority_filter in ["Low", "Medium", "High", "Urgent"]:
        rows = [row for row in rows if row.get("priority") == priority_filter]
    else:
        priority_filter = ""
    if search:
        term = search.casefold()
        rows = [
            row for row in rows
            if term in " ".join(
                str(row.get(field) or "")
                for field in ("subject", "customer_name", "customer_email")
            ).casefold()
        ]
    tickets = [format_ticket(row) for row in rows]
    return tickets, search, status_filter, priority_filter, counts


def sign_in_for_role(role):
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            flash("Enter your email and password.", "danger")
            return render_template("auth.html", portal=role, mode="login")

        try:
            client = get_supabase()
            result = client.auth.sign_in_with_password(
                {"email": email, "password": password}
            )
            if result.session is None:
                flash("Sign-in did not return an active session. Please try again.", "danger")
                return render_template("auth.html", portal=role, mode="login")
            clear_auth_session()
            session["access_token"] = result.session.access_token
            session["refresh_token"] = result.session.refresh_token
            session["csrf_token"] = secrets.token_urlsafe(32)
            client.postgrest.auth(result.session.access_token)
            profile_result = (
                client.table("profiles")
                .select("id, full_name, role")
                .eq("id", str(result.user.id))
                .limit(1)
                .execute()
            )
            profiles = profile_result.data or []
            if not profiles:
                clear_auth_session()
                flash("Your profile is not ready. Contact the support administrator.", "danger")
                return render_template("auth.html", portal=role, mode="login")
            account_role = profiles[0]["role"]
            if account_role != role:
                clear_auth_session()
                flash(
                    "This account belongs to the other portal. Use the correct sign-in page.",
                    "warning",
                )
                return render_template("auth.html", portal=role, mode="login")
            destination = "analyzer_dashboard" if role == "analyzer" else "customer_dashboard"
            return redirect(url_for(destination))
        except RuntimeError as error:
            flash(str(error), "danger")
        except Exception as error:
            app.logger.exception("Supabase sign-in failed")
            flash(auth_failure_message("sign you in", error), "danger")

    return render_template("auth.html", portal=role, mode="login")


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/customer/login", methods=["GET", "POST"])
def customer_login():
    return sign_in_for_role("customer")


@app.route("/analyzer/login", methods=["GET", "POST"])
def analyzer_login():
    return sign_in_for_role("analyzer")


@app.route("/customer/signup", methods=["GET", "POST"])
def customer_signup():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not full_name or not email or not password:
            flash("Complete all fields to create your customer account.", "danger")
        elif len(full_name) > 100 or len(email) > 150 or len(password) < 8:
            flash("Use a name under 100 characters, a valid email, and a password of at least 8 characters.", "danger")
        else:
            try:
                client = get_supabase()
                client.auth.sign_up(
                    {
                        "email": email,
                        "password": password,
                        "options": {
                            "data": {"full_name": full_name},
                            "email_redirect_to": EMAIL_CONFIRMATION_URL,
                        },
                    }
                )
                flash(
                    "Account created. Check your email to confirm your address, then sign in.",
                    "success",
                )
                return redirect(url_for("customer_login"))
            except RuntimeError as error:
                flash(str(error), "danger")
            except Exception as error:
                app.logger.exception("Customer registration failed")
                flash(auth_failure_message("create your account", error), "danger")
    return render_template("auth.html", portal="customer", mode="signup")


@app.route("/customer/resend-confirmation", methods=["POST"])
def resend_customer_confirmation():
    email = request.form.get("email", "").strip().lower()
    if not email:
        flash("Enter your email address so we can resend the confirmation link.", "danger")
        return redirect(url_for("customer_login"))
    try:
        get_supabase().auth.resend(
            {
                "type": "signup",
                "email": email,
                "options": {"email_redirect_to": EMAIL_CONFIRMATION_URL},
            }
        )
        flash(
            "If that account needs confirmation, Supabase has sent a new email. "
            "Check your inbox and spam folder.",
            "success",
        )
    except RuntimeError as error:
        flash(str(error), "danger")
    except Exception as error:
        app.logger.exception("Customer confirmation email resend failed")
        flash(auth_failure_message("send a confirmation email", error), "danger")
    return redirect(url_for("customer_login"))


@app.route("/auth/confirmed")
def auth_confirmed():
    return render_template("auth_confirmed.html")


@app.route("/logout", methods=["POST"])
def logout():
    refresh_token = session.get("refresh_token")
    if refresh_token and SUPABASE_URL and SUPABASE_KEY:
        try:
            client = get_supabase()
            client.auth.set_session(
                session.get("access_token", ""), refresh_token
            )
            client.auth.sign_out()
        except Exception:
            app.logger.exception("Supabase sign-out failed")
    clear_auth_session()
    flash("You have been signed out.", "success")
    return redirect(url_for("home"))


@app.route("/dashboard")
def dashboard():
    try:
        user = session.get("access_token") and authenticated_user()
    except Exception:
        app.logger.exception("Could not verify the dashboard session")
        flash("We could not verify your session. Please sign in again.", "danger")
        return redirect(url_for("customer_login"))
    if user is None:
        return redirect(url_for("customer_login"))
    destination = "analyzer_dashboard" if user["role"] == "analyzer" else "customer_dashboard"
    return redirect(url_for(destination))


@app.route("/customer/dashboard")
@role_required("customer")
def customer_dashboard():
    try:
        tickets, _, _, _, counts = tickets_for_dashboard()
        unread_replies = [ticket for ticket in tickets if ticket["has_unread_reply"]]
    except Exception:
        app.logger.exception("Could not load customer tickets")
        flash("Your tickets could not be loaded. Please try again.", "danger")
        tickets = []
        unread_replies = []
        counts = {"total": 0, "open": 0, "resolved": 0}
    return render_template(
        "customer_dashboard.html",
        tickets=tickets,
        total_tickets=counts["total"],
        open_tickets=counts["open"],
        resolved_tickets=counts["resolved"],
        unread_replies=unread_replies,
    )


@app.route("/analyzer/dashboard")
@role_required("analyzer")
def analyzer_dashboard():
    try:
        tickets, search, status_filter, priority_filter, counts = tickets_for_dashboard(analyzer=True)
    except Exception:
        app.logger.exception("Could not load analyzer ticket queue")
        flash("The ticket queue could not be loaded. Please try again.", "danger")
        tickets, search, status_filter, priority_filter = [], "", "", ""
        counts = {"total": 0, "open": 0, "in_progress": 0, "resolved": 0}
    return render_template(
        "dashboard.html",
        total_tickets=counts["total"],
        open_tickets=counts["open"],
        in_progress_tickets=counts["in_progress"],
        resolved_tickets=counts["resolved"],
        tickets=tickets,
        search=search,
        status_filter=status_filter,
        priority_filter=priority_filter,
    )


@app.route("/submit-ticket", methods=["GET", "POST"])
@role_required("customer")
def submit_ticket():
    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        description = request.form.get("description", "").strip()

        if not subject or not description:
            flash("Please complete all fields.", "danger")
            return redirect(url_for("submit_ticket"))
        if len(subject) > 200 or len(description) > 10000:
            flash("One or more fields are too long.", "danger")
            return redirect(url_for("submit_ticket"))

        ai_result = {
            "category": "Uncategorized",
            "priority": "Medium",
            "sentiment": "Unknown",
        }
        ai_reply = "AI-generated reply is currently unavailable."
        try:
            result = analyze_ticket(description, subject) or {}
            ai_result = {
                "category": result.get("category", "Uncategorized"),
                "priority": result.get("priority", "Medium"),
                "sentiment": result.get("sentiment", "Unknown"),
            }
        except Exception as error:
            log_ai_failure("ticket analysis", error)

        if ai_result["category"] != "Uncategorized":
            try:
                ai_reply = generate_suggested_reply(
                    subject, description, ai_result["category"], ai_result["priority"]
                )
            except Exception as error:
                log_ai_failure("reply generation", error)

        ticket = {
            "customer_id": g.user["id"],
            "customer_name": g.user["full_name"][:100],
            "customer_email": g.user["email"][:150],
            "subject": subject,
            "description": description,
            "category": ai_result["category"],
            "priority": ai_result["priority"],
            "status": "Open",
            "sentiment": ai_result["sentiment"],
            "ai_suggested_reply": ai_reply,
        }
        try:
            response = g.supabase.table("tickets").insert(ticket).select("*").execute()
            if not response.data:
                raise RuntimeError("Supabase did not return the created ticket.")
            created_ticket = format_ticket(response.data[0])
            flash(f"Ticket #{created_ticket['id']} submitted successfully!", "success")
            return redirect(
                url_for("ticket_success", ticket_id=created_ticket["id"])
            )
        except Exception:
            app.logger.exception("Supabase ticket submission failed")
            flash("We couldn't save your ticket. Please try again.", "danger")
            return redirect(url_for("submit_ticket"))

    return render_template("submit_ticket.html")


@app.route("/ticket/<ticket_id>/success")
@role_required("customer")
def ticket_success(ticket_id):
    try:
        ticket = fetch_ticket(ticket_id)
    except Exception:
        app.logger.exception("Could not load submitted ticket")
        flash("The ticket could not be loaded. Please try again.", "danger")
        return redirect(url_for("customer_dashboard"))
    if ticket is None:
        flash("Ticket not found.", "danger")
        return redirect(url_for("customer_dashboard"))
    return render_template("ticket_success.html", ticket=ticket)


@app.route("/ticket/<ticket_id>")
def ticket_details(ticket_id):
    try:
        user = authenticated_user()
    except Exception:
        app.logger.exception("Could not verify ticket access")
        flash("We could not verify your session. Please sign in again.", "danger")
        return redirect(url_for("customer_login"))
    if user is None:
        flash("Please sign in to view that ticket.", "warning")
        return redirect(url_for("customer_login"))
    try:
        ticket = fetch_ticket(ticket_id)
    except Exception:
        app.logger.exception("Could not load ticket details")
        flash("Ticket details could not be loaded. Please try again.", "danger")
        return redirect(
            url_for("analyzer_dashboard")
            if user["role"] == "analyzer"
            else url_for("customer_dashboard")
        )
    if ticket is None:
        flash("Ticket not found.", "danger")
        return redirect(
            url_for("analyzer_dashboard")
            if user["role"] == "analyzer"
            else url_for("customer_dashboard")
        )
    if user["role"] == "customer" and ticket["has_unread_reply"]:
        ticket["reply_is_new"] = True
        try:
            seen_result = (
                g.supabase.table("tickets")
                .update({"customer_reply_seen_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", ticket_id)
                .eq("customer_id", user["id"])
                .select("id")
                .execute()
            )
            if not seen_result.data:
                flash("The reply is available, but its notification could not be marked as read.", "warning")
            else:
                ticket["has_unread_reply"] = False
        except Exception:
            app.logger.exception("Could not mark the customer reply notification as read")
            flash("The reply is available, but its notification could not be marked as read.", "warning")
    return render_template("ticket_details.html", ticket=ticket)


@app.route("/ticket/<ticket_id>/update", methods=["POST"])
@role_required("analyzer")
def update_ticket(ticket_id):
    new_status = request.form.get("status", "")
    new_priority = request.form.get("priority", "")
    allowed_statuses = ["Open", "In Progress", "Resolved"]
    allowed_priorities = ["Low", "Medium", "High", "Urgent"]

    if new_status not in allowed_statuses or new_priority not in allowed_priorities:
        flash("Invalid status or priority.", "danger")
        return redirect(url_for("ticket_details", ticket_id=ticket_id))

    update = {"status": new_status, "priority": new_priority}
    if "ai_suggested_reply" in request.form:
        update["ai_suggested_reply"] = request.form.get(
            "ai_suggested_reply", ""
        ).strip()
    try:
        result = (
            g.supabase.table("tickets")
            .update(update)
            .eq("id", ticket_id)
            .select("id")
            .execute()
        )
        if not result.data:
            flash("Ticket not found or you do not have permission to update it.", "danger")
        else:
            flash("Ticket updated successfully!", "success")
    except Exception:
        app.logger.exception("Supabase ticket update failed")
        flash("Could not update the ticket. Please try again.", "danger")
    return redirect(url_for("ticket_details", ticket_id=ticket_id))


@app.route("/ticket/<ticket_id>/reply", methods=["POST"])
@role_required("analyzer")
def send_ticket_reply(ticket_id):
    reply = request.form.get("analyzer_reply", "").strip()
    if not reply:
        flash("Write a reply before sending it to the customer.", "danger")
        return redirect(url_for("ticket_details", ticket_id=ticket_id))
    if len(reply) > 5000:
        flash("The reply is too long. Keep it under 5,000 characters.", "danger")
        return redirect(url_for("ticket_details", ticket_id=ticket_id))

    try:
        result = (
            g.supabase.table("tickets")
            .update({
                "analyzer_reply": reply,
                "analyzer_reply_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", ticket_id)
            .select("id")
            .execute()
        )
        if not result.data:
            flash("Ticket not found or you do not have permission to reply.", "danger")
        else:
            flash("Reply sent. The customer will see it in their ticket notifications.", "success")
    except Exception as error:
        app.logger.exception("Could not send the analyzer reply")
        error_code = getattr(error, "code", None)
        error_message = getattr(error, "message", "") or ""
        if error_code == "PGRST204" and "analyzer_reply" in error_message:
            flash(
                "Supabase is missing the customer reply fields. Run the reply migration "
                "from supabase_schema.sql in the Supabase SQL Editor, then retry.",
                "danger",
            )
        else:
            flash("Could not send the reply. Please try again.", "danger")
    return redirect(url_for("ticket_details", ticket_id=ticket_id))


@app.route("/ticket/<ticket_id>/edit", methods=["POST"])
@role_required("customer")
def edit_customer_ticket(ticket_id):
    subject = request.form.get("subject", "").strip()
    description = request.form.get("description", "").strip()
    if not subject or not description:
        flash("Please complete the subject and description.", "danger")
        return redirect(url_for("ticket_details", ticket_id=ticket_id))
    if len(subject) > 200 or len(description) > 10000:
        flash("One or more fields are too long.", "danger")
        return redirect(url_for("ticket_details", ticket_id=ticket_id))

    try:
        result = (
            g.supabase.table("tickets")
            .update({"subject": subject, "description": description})
            .eq("id", ticket_id)
            .eq("customer_id", g.user["id"])
            .neq("status", "Resolved")
            .select("id")
            .execute()
        )
        if not result.data:
            flash(
                "This ticket cannot be edited. Resolved tickets are read-only.",
                "warning",
            )
        else:
            flash("Your ticket details were updated.", "success")
    except Exception:
        app.logger.exception("Customer ticket edit failed")
        flash("Could not update your ticket. Please try again.", "danger")
    return redirect(url_for("ticket_details", ticket_id=ticket_id))


@app.route("/analytics")
@role_required("analyzer")
def analytics():
    try:
        rows = (
            g.supabase.table("tickets")
            .select("status, category, priority, sentiment")
            .execute()
            .data or []
        )

        def chart_data(field, fallback_label="Unknown"):
            counts = {}
            for row in rows:
                label = row.get(field) or fallback_label
                counts[label] = counts.get(label, 0) + 1
            return {
                "labels": list(counts.keys()),
                "values": list(counts.values()),
            }

        datasets = {
            "total_tickets": len(rows),
            "status_data": chart_data("status"),
            "category_data": chart_data("category", "Uncategorized"),
            "priority_data": chart_data("priority"),
            "sentiment_data": chart_data("sentiment"),
        }
    except Exception:
        app.logger.exception("Could not load support analytics")
        flash("Analytics could not be loaded. Please try again.", "danger")
        datasets = {
            "total_tickets": 0,
            "status_data": {"labels": [], "values": []},
            "category_data": {"labels": [], "values": []},
            "priority_data": {"labels": [], "values": []},
            "sentiment_data": {"labels": [], "values": []},
        }
    return render_template("analytics.html", **datasets)


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
