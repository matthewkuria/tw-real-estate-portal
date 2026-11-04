"""
Next.js routes that notifications link to. Keep these in sync with the
frontend; emails turn them into full URLs using FRONTEND_URL.
"""

PREFERENCES = "/settings/notifications"
ACCOUNT = "/account"


def update(update_id):
    return f"/community/updates/{update_id}"


def thread(thread_id):
    return f"/community/threads/{thread_id}"


def message(thread_id, message_id):
    return f"/community/threads/{thread_id}?message={message_id}"


def member(user_id):
    return f"/admin/members/{user_id}"


def payment(payment_id):
    return f"/payments/{payment_id}"


def plot(plot_id):
    return f"/plots/{plot_id}"
