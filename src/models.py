from decimal import Decimal
from typing import Literal, NewType

import msgspec

TariffId = NewType("TariffId", str)
AddonId = NewType("AddonId", str)
SubscriberId = NewType("SubscriberId", str)

class Rates(msgspec.Struct, frozen=True):
    call: Decimal
    data: Decimal
    sms: Decimal

class Tariff(msgspec.Struct, frozen=True):
    id: TariffId
    name: str
    fee: Decimal
    minutes: int
    gb: Decimal
    sms: int
    overage: Rates
    night_unlimited: bool = False

class TariffsConfig(msgspec.Struct, frozen=True):
    curency: str
    tariffs: tuple[Tariff, ...]
    roaming_rates: Rates

class Addon(msgspec.Struct, frozen=True):
    id: AddonId
    name: str
    fee: Decimal
    minutes: int
    gb: Decimal
    sms: int

class SubscriberProfile(msgspec.Struct, frozen=True):
    id: SubscriberId
    current_tariff_id: str
    months: tuple[str, ...]
    usage_file: str
