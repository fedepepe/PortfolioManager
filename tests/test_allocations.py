import pytest

import portfolio_manager.storage.queries as sql
from portfolio_manager.config.allocations import (
    ASSET_CLASSES,
    PRESETS,
    Allocation,
    AllocationInstrument,
    Bucket,
    percent_text,
)
from portfolio_manager.optimization.settings import AllocationStrats, OptimizationSettings


def balanced() -> Allocation:
    # 60% equity split by region (Switzerland 20%, Global 80%), 40% bonds not split
    return Allocation(
        name='Balanced',
        class_targets={'Equity': 0.6, 'Fixed Income': 0.4, 'Crypto': 0.0},
        splits={'Equity': 'region'},
        buckets=[Bucket('Equity', 'Switzerland', 0.2), Bucket('Equity', 'Global', 0.8)],
        instruments=[
            AllocationInstrument('CHSPI.SW', 'CH0237935652', 'Equity', 'Switzerland', 1.0),
            AllocationInstrument('VWCE.DE', 'IE00BK5BQT80', 'Equity', 'Global', 0.5),
            AllocationInstrument('EUNL.DE', 'IE00B4L5Y983', 'Equity', 'Global', 0.5),
            AllocationInstrument('IEF', None, 'Fixed Income', None, 1.0),
        ],
        weight_mode='manual',
        settings=OptimizationSettings(method=AllocationStrats.MIN_VAR, class_tolerance=0.1),
        rebalancing_freq='Y',
    )


def test_manual_weights_of_the_instruments():
    weights = balanced().target_weights()
    assert weights == pytest.approx({'CHSPI.SW': 0.12, 'VWCE.DE': 0.24, 'EUNL.DE': 0.24, 'IEF': 0.4})
    assert sum(weights.values()) == pytest.approx(1.0)


def test_asset_classes_and_class_bounds_for_an_optimization():
    allocation = balanced()
    assert allocation.asset_classes()['CHSPI.SW'] == 'Equity'
    # the classes with a target, plus or minus the tolerance (Crypto at 0% has none)
    assert allocation.class_bounds() == {'Equity': pytest.approx((0.5, 0.7)), 'Fixed Income': pytest.approx((0.3, 0.5))}


@pytest.mark.parametrize(
    'change, message',
    [
        (lambda a: a.class_targets.update({'Equity': 0.5}), 'add up to 90%, not 100%'),
        (lambda a: a.buckets.pop(), 'Equity: the weights of its sub-classes'),
        (lambda a: a.instruments.pop(), 'Fixed Income: no instrument chosen'),
        (lambda a: a.instruments.pop(1), 'Equity - Global: the weights of its instruments'),
        (lambda a: a.splits.update({'Equity': 'continent'}), 'unknown split'),
        (lambda a: setattr(a, 'rebalancing_freq', 'W'), 'rebalancing frequency'),
        (lambda a: setattr(a, 'name', ' '), 'needs a name'),
    ],
)
def test_allocation_that_cannot_be_backtested(change, message):
    allocation = balanced()
    change(allocation)
    with pytest.raises(ValueError, match=message):
        allocation.validate()


def test_all_the_problems_listed_in_the_order_of_the_steps():
    allocation = balanced()
    allocation.class_targets['Equity'] = 0.5
    allocation.instruments.pop()
    assert allocation.problems() == [
        'The asset class weights add up to 90%, not 100%',
        'Fixed Income: no instrument chosen',
    ]
    assert balanced().problems() == []


def test_parts_of_a_class_without_weight_not_checked():
    allocation = balanced()
    allocation.class_targets = {'Equity': 0.0, 'Fixed Income': 1.0}
    allocation.buckets = [Bucket('Equity', 'Switzerland', 0.2)]  # 20% only, but the class has no weight
    assert allocation.problems() == []


def test_allocation_as_plain_data_and_back():
    import json

    allocation = balanced()
    allocation.settings.max_vol = None
    data = json.loads(json.dumps(allocation.to_dict()))  # as kept in the browser
    assert Allocation.from_dict(data) == allocation


def test_percentages_with_their_decimals():
    assert [percent_text(w) for w in (0.3, 0.305, 0.2425, 1.0, 0.0)] == ['30%', '30.5%', '24.25%', '100%', '0%']
    allocation = balanced()
    allocation.class_targets['Equity'] = 0.595
    assert allocation.problems() == ['The asset class weights add up to 99.5%, not 100%']


def test_presets_add_up_to_100_percent_of_known_classes():
    for targets in PRESETS.values():
        assert sum(targets.values()) == pytest.approx(1.0) and set(targets) <= set(ASSET_CLASSES)


def test_optimized_weights_need_no_manual_weights():
    allocation = balanced()
    allocation.weight_mode = 'optimize'
    allocation.instruments[1] = AllocationInstrument('VWCE.DE', 'IE00BK5BQT80', 'Equity', 'Global', 0.0)
    allocation.validate()  # the weights within the Global bucket no longer add up to 100%: not needed


def test_allocations_saved_listed_replaced_and_deleted(temp_db):
    sql.save_allocation('Portfolio CHF', balanced())
    growth = balanced()
    growth.name = 'Growth'
    growth.class_targets = {'Equity': 0.8, 'Fixed Income': 0.2}
    sql.save_allocation('Portfolio CHF', growth)
    assert sql.list_allocations('Portfolio CHF') == ['Growth', 'Balanced']  # the last saved first
    assert sql.list_allocations('Portfolio EUR') == []
    assert sql.query_allocation('Portfolio CHF', 'Balanced') == balanced()  # everything comes back
    # saving under the same name replaces it
    growth.weight_mode = 'optimize'
    growth.instruments = growth.instruments[:2] + growth.instruments[3:]
    growth.buckets = [Bucket('Equity', 'Switzerland', 0.5), Bucket('Equity', 'Global', 0.5)]
    sql.save_allocation('Portfolio CHF', growth)
    saved = sql.query_allocation('Portfolio CHF', 'Growth')
    assert saved.weight_mode == 'optimize' and [i.ticker for i in saved.instruments] == ['CHSPI.SW', 'VWCE.DE', 'IEF']
    assert saved.buckets == growth.buckets
    sql.delete_allocation('Portfolio CHF', 'Growth')
    assert (
        sql.list_allocations('Portfolio CHF') == ['Balanced']
        and sql.query_allocation('Portfolio CHF', 'Growth') is None
    )


def test_invalid_allocation_is_not_saved(temp_db):
    allocation = balanced()
    allocation.class_targets['Equity'] = 0.9
    with pytest.raises(ValueError):
        sql.save_allocation('Portfolio CHF', allocation)
    assert sql.list_allocations('Portfolio CHF') == []
