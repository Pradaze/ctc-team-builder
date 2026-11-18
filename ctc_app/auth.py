"""Access control.

Sign-in is either OpenID Connect (Google / Microsoft, via st.login) or a shared
password. Either way, what someone can do depends on their role in the
[access] section of secrets.toml: admin > reviewer > viewer.
"""

import hashlib
import hmac
import os
from collections.abc import Mapping

import streamlit as st

from . import state

ROLES = ["viewer", "reviewer", "admin"]


def _access():
    try:
        return st.secrets.get("access", {}) or {}
    except Exception:
        return {}


def _auth_section():
    try:
        return st.secrets.get("auth", None)
    except Exception:
        return None


def _lower(xs):
    return {str(x).strip().lower() for x in (xs or [])}


def role_for(email):
    if not email:
        return None
    email = email.strip().lower()
    domain = email.split("@")[-1]
    acc = _access()
    for role in ("admin", "reviewer", "viewer"):
        if email in _lower(acc.get(role + "s")) or domain in _lower(acc.get(role + "_domains")):
            return role
    return None


def can(user, needed):
    return ROLES.index(user["role"]) >= ROLES.index(needed)


def _providers(auth):
    named = [k for k, v in auth.items() if isinstance(v, Mapping)]
    return named or [None]


def _label(provider):
    return {"google": "Sign in with Google", "microsoft": "Sign in with Microsoft",
            None: "Sign in"}.get(provider, f"Sign in with {provider.title()}")


def _gate(title, body):
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown(f"## {title}")
        st.write(body)
        return mid


def require_user():
    """Return {'email', 'name', 'role'} or stop the page with a sign-in screen."""
    auth = _auth_section()
    acc = _access()

    if auth:  # OpenID Connect
        if not getattr(st.user, "is_logged_in", False):
            box = _gate("Cleantech Challenge 2027 team builder",
                        "Sign in with the account your organiser invite was sent to.")
            with box:
                for p in _providers(auth):
                    if st.button(_label(p), type="primary", width="stretch", key=f"login_{p}"):
                        st.login(p) if p else st.login()
            st.stop()
        email = (st.user.get("email") or "").lower()
        name = st.user.get("name") or email
        role = role_for(email)
        if not role:
            box = _gate("No access yet",
                        f"You're signed in as {email}, but this address isn't on the organiser list. "
                        "Ask an admin to add it to the [access] section of the app's secrets.")
            with box:
                st.button("Sign out", on_click=st.logout)
            st.stop()
        return {"email": email, "name": name, "role": role, "method": "sso"}

    if acc.get("password_sha256"):  # shared password
        if st.session_state.get("_pw_ok"):
            return {"email": "organiser (password)", "name": "Organiser",
                    "role": acc.get("password_role", "admin"), "method": "password"}
        box = _gate("Cleantech Challenge 2027 team builder", "Enter the organiser password.")
        with box:
            with st.form("pw"):
                pw = st.text_input("Password", type="password")
                ok = st.form_submit_button("Sign in", type="primary")
            if ok:
                digest = hashlib.sha256(pw.encode()).hexdigest()
                if hmac.compare_digest(digest, str(acc["password_sha256"]).lower()):
                    st.session_state["_pw_ok"] = True
                    st.rerun()
                st.error("That password isn't right.")
        st.stop()

    if os.environ.get("CTC_LOCAL_MODE") == "1":  # explicit local-only mode
        return {"email": "local", "name": "Local user", "role": "admin", "method": "local"}

    _gate("Access isn't set up",
          "Add an [auth] or a password to .streamlit/secrets.toml (see secrets.toml.example), "
          "or run with CTC_LOCAL_MODE=1 on your own computer.")
    st.stop()


def sign_out_button(user):
    if user["method"] == "sso":
        st.button("Sign out", on_click=st.logout, width="stretch")
    elif user["method"] == "password":
        if st.button("Sign out", width="stretch"):
            st.session_state.pop("_pw_ok", None)
            st.rerun()
