class DomainError(Exception):
    """Base for business-rule violations raised by the service layer."""

    default_code = "error"


class InvalidAmountError(DomainError):
    default_code = "invalid_amount"


class InsufficientFundsError(DomainError):
    default_code = "insufficient_funds"


class SameWalletTransferError(DomainError):
    default_code = "same_wallet_transfer"


class WalletNotFoundError(DomainError):
    """Raised when a wallet doesn't exist, or belongs to another tenant.

    Deliberately used for both cases — see services.transfer — so a
    cross-tenant transfer attempt never confirms the other wallet exists.
    """

    default_code = "wallet_not_found"
