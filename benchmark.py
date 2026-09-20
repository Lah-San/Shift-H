"""Run the engine over every booked-leave scenario and report outcomes, rule hits and speed.
Writes data/benchmark_results.json and data/benchmark_scenarios.csv.
Run:  python benchmark.py [--limit N]"""
import argparse, collections, datetime as dt, json, os, sys, time
import polars as pl
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leavecover import RuleBook, DataStore, Engine

FAMILY_TO_TYPE = {'ANNUAL': 'ANNUAL', 'LONG_SERVICE': 'LONG_SERVICE', 'PERSONAL_SICK': 'SICK', 'PROF_DEV_STUDY': 'PROF_DEV', 'ADO_TOIL_ONCALL': 'ADO',
                  'PUBLIC_HOLIDAY_TOIL': 'TOIL', 'PARENTAL': 'PARENTAL', 'UNPAID': 'UNPAID', 'CULTURAL': 'CULTURAL', 'OTHER': 'UNPAID'}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--limit', type=int, default=0); ap.add_argument('--alternatives', action='store_true')
    a = ap.parse_args()
    rb = RuleBook(); store = DataStore(rulebook=rb); eng = Engine(store, rb)
    sc = pl.read_parquet(os.path.join(store.clean_dir, 'scenarios.parquet')).filter(pl.col('leave_start_date') > rb.today())
    if a.limit:
        sc = sc.head(a.limit)
    rows, outcomes, codes, tiers, times = [], collections.Counter(), collections.Counter(), collections.Counter(), []
    t0 = time.perf_counter()
    for r in sc.iter_rows(named=True):
        lt = FAMILY_TO_TYPE.get(r['leave_family'], 'ANNUAL')
        t = time.perf_counter()
        d = eng.evaluate(r['employee_id'], lt, r['leave_start_date'], r['leave_end_date'], '', with_alternatives=a.alternatives, already_booked=True)
        ms = (time.perf_counter() - t) * 1000
        times.append(ms); outcomes[d.outcome] += 1
        for c in d.breached_codes:
            codes[c] += 1
        for c in d.cover:
            if c.candidate:
                tiers[c.candidate.tier] += 1
        rows.append({'employee_id': r['employee_id'], 'leave_type': lt, 'start': str(r['leave_start_date']), 'end': str(r['leave_end_date']), 'outcome': d.outcome,
                     'breached': '|'.join(d.breached_codes), 'shifts': d.chart['summary']['shifts'], 'covered': d.chart['summary']['covered'], 'uncovered': d.chart['summary']['uncovered'],
                     'not_needed': d.chart['summary']['not_needed'], 'forecast': d.forecast_mode, 'ms': round(ms, 2)})
    total = time.perf_counter() - t0
    times.sort()
    res = {'scenarios': len(rows), 'total_seconds': round(total, 2), 'per_scenario_ms': {'mean': round(sum(times) / len(times), 2), 'p50': round(times[len(times) // 2], 2), 'p95': round(times[int(len(times) * 0.95)], 2), 'max': round(times[-1], 2)},
           'outcomes': dict(outcomes), 'rule_hits': dict(codes.most_common()), 'cover_by_tier': dict(tiers),
           'shifts_total': sum(x['shifts'] for x in rows), 'shifts_covered': sum(x['covered'] for x in rows), 'shifts_uncovered': sum(x['uncovered'] for x in rows), 'shifts_not_needed': sum(x['not_needed'] for x in rows),
           'rules_version': rb.version, 'run_at': dt.datetime.now().isoformat(timespec='seconds')}
    os.makedirs('data', exist_ok=True)
    json.dump(res, open(os.path.join('data', 'benchmark_results.json'), 'w'), indent=2)
    pl.DataFrame(rows).write_csv(os.path.join('data', 'benchmark_scenarios.csv'))
    print(json.dumps(res, indent=2))


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
