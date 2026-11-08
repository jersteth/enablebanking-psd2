"""Application-level errors. API errors live in ``psd2demo.eb.errors``."""


class DemoError(Exception):
    """Base for errors reported as a failed demo step."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigError(DemoError):
    def __init__(self, problems: list[str]):
        super().__init__("invalid configuration: " + "; ".join(problems),
                         hint="see config.example.toml and documentation/enablebanking-setup.md")
        self.problems = problems


class UsageError(DemoError):
    """Invalid command-line usage; exit code 2 like ConfigError."""


class ConsentError(DemoError):
    pass


class ConsentDenied(ConsentError):
    def __init__(self, error: str, description: str | None):
        msg = f"authorization failed at the bank: {error}"
        if description:
            msg += f" ({description})"
        super().__init__(msg, hint="retry with --fresh; check the account is selected at the bank")


class StateMismatch(ConsentError):
    def __init__(self):
        super().__init__("callback state does not match the authorization request",
                         hint="paste the redirect URL from this run's authorization, not an older one")


class CallbackTimeout(ConsentError):
    def __init__(self, timeout: int):
        super().__init__(f"no callback received within {timeout} s",
                         hint="rerun and complete the bank authorization faster, or raise callback_timeout")


class BankNotFound(DemoError):
    def __init__(self, name: str, country: str, candidates: list[str]):
        msg = f"bank {name!r} not found for country {country}"
        hint = ("candidates: " + ", ".join(candidates)) if candidates else None
        super().__init__(msg, hint=hint)


class AccountNotFound(DemoError):
    def __init__(self, iban_masked: str, accounts: list[str]):
        listing = "; ".join(accounts) if accounts else "none"
        super().__init__(
            f"account {iban_masked} not in the authorized session (session accounts: {listing})",
            hint="check the account is linked in the Enable Banking control panel and selected "
                 "at the bank during authorization; rerun with --fresh",
        )
