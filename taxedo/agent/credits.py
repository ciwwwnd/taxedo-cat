class CreditsExhausted(RuntimeError):
    pass


def is_exhausted(error) -> bool:
    message = str(error).lower()
    return "credit balance" in message and "too low" in message


class CreditNotifier:
    def __init__(self):
        self.alert_sent = False

    def recovered(self):
        self.alert_sent = False

    def check_api_error(self, error):
        if is_exhausted(error) and not self.alert_sent:
            return {"type": "out_of_credits"}
        return None

    def delivered(self):
        self.alert_sent = True


def format_credit_alert():
    return (
        "<b>API credits exhausted</b>\n\n"
        "Receipt analysis is unavailable until you top up your Anthropic credits."
    )
