"""Money-movement logic: the only place wallet balances are mutated.

Every function here follows the same shape — lock the wallet row(s) with
select_for_update() inside an atomic block, check business rules against the
locked (not stale) balance, then write a Transaction ledger row and update the
cached balance together. The ledger row is the source of truth; `balance` on
Wallet is a cache that is only ever written alongside it, in the same
transaction, so it can never drift.
"""

import uuid

from django.db import transaction as db_transaction

from .exceptions import (
    InsufficientFundsError,
    InvalidAmountError,
    SameWalletTransferError,
    WalletNotFoundError,
)
from .models import Transaction, Wallet


def _validate_amount(amount):
    if amount <= 0:
        raise InvalidAmountError("amount must be a positive integer.")


def deposit(*, tenant, wallet_id, amount, idempotency_key, metadata=None):
    _validate_amount(amount)

    with db_transaction.atomic():
        try:
            wallet = Wallet.objects.select_for_update().get(pk=wallet_id, tenant=tenant)
        except Wallet.DoesNotExist:
            raise WalletNotFoundError(f"No wallet {wallet_id} for this tenant.")

        new_balance = wallet.balance + amount
        txn = Transaction.objects.create(
            tenant=tenant,
            wallet=wallet,
            type=Transaction.Type.DEPOSIT,
            amount=amount,
            balance_after=new_balance,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        wallet.balance = new_balance
        wallet.save(update_fields=["balance"])

    return txn


def withdraw(*, tenant, wallet_id, amount, idempotency_key, metadata=None):
    _validate_amount(amount)

    with db_transaction.atomic():
        try:
            wallet = Wallet.objects.select_for_update().get(pk=wallet_id, tenant=tenant)
        except Wallet.DoesNotExist:
            raise WalletNotFoundError(f"No wallet {wallet_id} for this tenant.")

        if wallet.balance < amount:
            raise InsufficientFundsError(
                f"Wallet balance {wallet.balance} is less than requested {amount}."
            )

        new_balance = wallet.balance - amount
        txn = Transaction.objects.create(
            tenant=tenant,
            wallet=wallet,
            type=Transaction.Type.WITHDRAWAL,
            amount=amount,
            balance_after=new_balance,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        wallet.balance = new_balance
        wallet.save(update_fields=["balance"])

    return txn


def transfer(*, tenant, from_wallet_id, to_wallet_id, amount, idempotency_key, metadata=None):
    _validate_amount(amount)

    if str(from_wallet_id) == str(to_wallet_id):
        raise SameWalletTransferError("from_wallet and to_wallet must be different.")

    with db_transaction.atomic():
        # Lock both wallets in a single, id-ordered query so any two concurrent
        # transfers touching this pair (in either direction) always acquire
        # their row locks in the same order — this is what prevents deadlocks.
        wallets = list(
            Wallet.objects.select_for_update()
            .filter(id__in=[from_wallet_id, to_wallet_id], tenant=tenant)
            .order_by("id")
        )
        wallets_by_id = {str(w.id): w for w in wallets}

        if len(wallets_by_id) != 2:
            # Covers: wallet doesn't exist, OR belongs to another tenant.
            # Same error either way, so a cross-tenant attempt can't be used
            # to probe whether a wallet id exists on another tenant.
            raise WalletNotFoundError(
                "Both from_wallet and to_wallet must exist for this tenant."
            )

        from_wallet = wallets_by_id[str(from_wallet_id)]
        to_wallet = wallets_by_id[str(to_wallet_id)]

        if from_wallet.balance < amount:
            raise InsufficientFundsError(
                f"Wallet balance {from_wallet.balance} is less than requested {amount}."
            )

        related_transfer_id = uuid.uuid4()

        new_from_balance = from_wallet.balance - amount
        out_txn = Transaction.objects.create(
            tenant=tenant,
            wallet=from_wallet,
            type=Transaction.Type.TRANSFER_OUT,
            amount=amount,
            balance_after=new_from_balance,
            idempotency_key=idempotency_key,
            related_transfer_id=related_transfer_id,
            metadata=metadata,
        )
        from_wallet.balance = new_from_balance
        from_wallet.save(update_fields=["balance"])

        new_to_balance = to_wallet.balance + amount
        in_txn = Transaction.objects.create(
            tenant=tenant,
            wallet=to_wallet,
            type=Transaction.Type.TRANSFER_IN,
            amount=amount,
            balance_after=new_to_balance,
            idempotency_key=idempotency_key,
            related_transfer_id=related_transfer_id,
            metadata=metadata,
        )
        to_wallet.balance = new_to_balance
        to_wallet.save(update_fields=["balance"])

    return out_txn, in_txn
