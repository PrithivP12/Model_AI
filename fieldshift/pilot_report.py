"""Render the completed frozen experiment without fitting or tuning anything."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from .crop_performance import OUT


def render():
    r=json.loads((OUT/'report.json').read_text())
    e=json.loads((OUT.parent/'evaluation-only/report.json').read_text())
    a=json.loads((OUT.parent/'matched_data_audit.json').read_text())
    d=pd.read_csv(OUT.parent/'matched_features.csv',dtype={'county_fips':str})
    nasa=r['final_test']['history_power_nldas']['all_counties']
    rule=e['strict_same_nitrogen']['untrained_fixed_rule']
    names={'baseline':'Historical trend forecast','history':'Yield-history ML',
           'history_power':'Yield history + POWER ML','history_power_nldas':'Yield history + POWER + NLDAS ML'}
    lines=['# Crop performance and rotation evaluation','',
           '**A crop-yield forecast was trained and tested. A reliable crop-rotation strategy model has not yet been established.**','',
           'Rotation outcomes were used only for the independent comparison below. They were not inputs, training labels, tuning targets or sources of a fitted yield bonus.','',
           '## Yield forecast results','',
           f'The matched dataset has {a["records"]:,} county/crop/year records across {a["counties"]} Illinois counties, 2006–2025. It contains corn-grain, soybean and winter-wheat yields. Original NASS observations were joined with 58 POWER grid points and 26 original NLDAS monthly files. The 110-point demonstration observation table was not replicated into training data.','',
           'Forecasts are made as of September 1 of the year before harvest, before winter-wheat sowing and next spring’s crops. Weather inputs stop in June of that preceding year. Yield histories and fitting labels stop at harvest year minus two. The very long lead time deliberately excludes growing-season weather. Weather and NLDAS are sampled at county interior points rather than spatially averaged over cropland. These are retrospective revised products, not a reconstruction of what was published on each historical decision date.','',
           'Twenty-one ML configurations and six historical baselines were compared over three validation years (2020–2022), using 81 validation fits/evaluations. A fixed geographic band of 38 counties was excluded from fitting and model selection. Those counties still supply their own earlier yield histories as prediction inputs, so this is not a no-history cold start test. The three feature-group winners and the baseline were frozen before calibration in 2023 and scoring 2024–2025.','',
           '| Model | Validation average error | Final test average error | Within 10% of observed yield | Within 20% |',
           '|---|---:|---:|---:|---:|']
    for k,name in names.items():
        v=r['selections'][k]['macro_mape_percent'];m=r['final_test'][k]['all_counties']
        lines.append(f'| {name} | {v:.2f}% | {m["macro_mape_percent"]:.2f}% | {100*m["within_10_percent"]:.1f}% | {100*m["within_20_percent"]:.1f}% |')
    lines+=['',
        'Average error above is crop-macro mean absolute percentage error (MAPE): calculate each crop’s MAPE, then average the three. Lower is better. The tolerance columns count individual predictions. **MAPE is not an accuracy percentage, and 100 minus MAPE is not model accuracy.**','',
        'The historical trend forecast won validation and therefore remains the preselected overall method. The yield-history ML performed better on the final test, but choosing it now would reuse the test for selection. NASA features improved over the baseline in the final years without consistently beating history-only ML. NLDAS did not improve validation over POWER-only ML. More data sources did not produce uniformly better predictions.','',
        'The final test has 356 reported county/crop/year records (179 in 2024 and 177 in 2025). Missing or suppressed county outcomes were excluded, not invented. This affects representativeness. All model families were evaluated on the same test records.','',
        '### NASA-enriched model by crop','',
        '| Crop | Test records | MAE (bushels/acre) | MAPE | R² | Within 10% |',
        '|---|---:|---:|---:|---:|---:|']
    for crop,m in nasa['per_crop'].items():
        lines.append(f'| {crop} | {m["records"]} | {m["mae_bu_acre"]:.2f} | {m["mape_percent"]:.2f}% | {m["r2"]:.3f} | {100*m["within_10_percent"]:.1f}% |')
    n=r['final_test']['history_power_nldas']
    gain=r['baseline_comparisons']['history_power_nldas']
    interval=gain['county_cluster_bootstrap_95pct_interval']
    lines+=['',
        f'The NASA-enriched model’s error was {n["seen_counties"]["macro_mape_percent"]:.2f}% in counties seen during fitting and {n["unseen_in_fitting_counties"]["macro_mape_percent"]:.2f}% in geographically held-out counties. Its error rose from {n["by_year"]["2024"]["macro_mape_percent"]:.2f}% in 2024 to {n["by_year"]["2025"]["macro_mape_percent"]:.2f}% in 2025. Winter-wheat R² is negative, so its predictions failed the observed-test-mean variance benchmark.','',
        f'Against the validation-selected historical baseline, its final-test MAPE gain was {gain["macro_mape_gain_percentage_points"]:.2f} percentage points. A county-cluster bootstrap interval was {interval[0]:.2f}–{interval[1]:.2f} points. This interval does not capture uncertainty across future years, other states or global climates. Nominal 90% intervals covered {100*n["interval_empirical_coverage"]:.1f}% of these test observations; this is empirical coverage, not a guarantee.','',
        '## Independent rotation outcome comparison','',
        'A structural sequence-sensitivity check and a separate rule comparison used a [USDA ARS experiment in Brookings, South Dakota](https://doi.org/10.15482/USDA.ADC/27207915.v1), using the original “Master file” sheet, header row 7. The source has 304 plot/year records during 2017–2020, including 96 maize harvests. It was kept in `evaluation-only/` and never loaded by model fitting or selection. The saved model-selection and training-report hashes are recorded in the external report.','',
        '**The ML predicts no rotation-specific difference when current crop, location, year and permitted inputs are identical. Its rotation-direction decision coverage is 0%, and its directional accuracy is unavailable. This is a structural sensitivity check, not a forecast of absolute South Dakota yields.** Giving tied predictions an arbitrary alphabetical winner would create a false accuracy number. The zero-difference diagnostic has 2.03 Mg/ha RMSE against the 88 strict observed maize-yield contrasts.','',
        'A separately declared, untrained rule ranks maize-containing sequences by the fraction of years occupied by other crops. It has no calibrated yield effect and does not use trial outcomes to set weights. The rule ties all four-year rotations containing one maize year. Comparisons below hold year, experimental block and exact applied nitrogen constant, and exclude the separately labeled CornHigh treatment.','',
        f'- Strict eligible comparisons: {e["strict_same_nitrogen"]["pairs"]}, drawn from 16 year/block groups at one site.',
        f'- Choices made by the rule: {rule["decisive_non_tie_pairs"]}; tied/abstained: {rule["score_ties"]}. Decision coverage: {100*rule["decision_coverage"]:.0f}%.',
        '- Correct directional comparisons: 41 of 44. Conditional agreement: 93.2%.',
        '- That 93.2% describes the simple rule only on comparisons where it made a choice. It is not ML accuracy, does not count the ties as successes, and does not establish reliable general crop-rotation performance.','',
        'An additional broad comparison includes continuous maize and differing nitrogen rates: the rule agrees on 132 of 144 decided comparisons, with 60% decision coverage. That combines management differences and cannot isolate rotation effects. It is secondary and should not replace the strict comparison.','',
        'These pairwise comparisons share plots, years and location. They are not 88 independent experiments. The test covers maize harvests, not total sequence production, profit, disease risk, microbiome recovery or pollinator benefit. The study abstract was read during discovery, so this is exploratory external evaluation rather than a blind confirmatory test.','',
        'The originally planned [11-experiment Dryad dataset](https://doi.org/10.6078/D1H409) could be catalogued but its file download returned 403 and its API download returned 401. The fallback and unchanged scoring rule were recorded before computing rotation metrics. A [second candidate from Northeast China](https://doi.org/10.5281/zenodo.17207836) was downloaded but not scored: its repository does not provide a verified treatment-code/unit mapping and its yield table contains summary statistics rather than matched independent plot records. It is not counted as another validation success.','',
        '## What this model can support','',
        'It provides a reproducible Illinois county crop-yield benchmark with saved models and uncertainty estimates. It cannot yet predict how changing one rotation into another changes yield. No raw crop SNPs, local pathogen-presence assays, field microbiome or field pollinator abundance were available with legitimate matching yield labels for this experiment. Those sources remain contextual rather than copied into unrelated training rows.','',
        'To obtain rotation-strategy accuracy under the test-only outcome constraint, a future model needs a defensible independently learned or mechanistic state transition model, measured initial field conditions, and evaluation at multiple independent rotation trials. Rotation test outcomes must remain outside fitting and tuning. The present result does not justify a “best rotation” claim.','',
        '## Reproduce','',
        '```sh',
        '.venv/bin/python -m fieldshift.pilot_sources',
        'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 .venv/bin/python -m fieldshift.crop_performance',
        '.venv/bin/python -m fieldshift.rotation_evaluation',
        '.venv/bin/python -m fieldshift.pilot_report',
        '```','',
        'The training command refuses to overwrite final results. Preserve them before starting a separately versioned experiment. Existing raw and processed sources allow offline checks. The external source spreadsheet stays unchanged; its values were read into a test-only CSV. Data source: Bansal, Wang and Osborne, USDA ARS, DOI 10.15482/USDA.ADC/27207915.v1, portal license CC BY 4.0.','',
        'Key artifacts: `results/protocol.json`, `results/selection_frozen.json`, `results/report.json`, `results/*_test_predictions.csv`, `evaluation-only/report.json`, `evaluation-only/strict_pairs.csv`, and `matched_data_audit.json`.','',
        '![Final-test crop-yield forecasts](results/yield_evaluation.png)']
    (OUT.parent/'MODEL_EVALUATION.md').write_text('\n'.join(lines)+'\n')
    make_plot(r,names)
    print('Saved',OUT.parent/'MODEL_EVALUATION.md')


def make_plot(report,names):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    p=pd.read_csv(OUT/'history_power_nldas_test_predictions.csv')
    fig,axes=plt.subplots(2,2,figsize=(11,8.6),layout='constrained')
    colors={2024:'#267b91',2025:'#c76d39'}
    for ax,crop in zip(axes.flat,list(p.crop.unique())):
        d=p[p.crop==crop]
        for year,g in d.groupby('year'):ax.scatter(g.y,g.prediction,s=23,color=colors[year],alpha=.7,label=str(year))
        lo=min(d.y.min(),d.prediction.min());hi=max(d.y.max(),d.prediction.max())
        ax.plot([lo,hi],[lo,hi],color='#555555',ls='--',lw=1)
        ax.set(xlabel='Observed yield (bushels/acre)',ylabel='Predicted yield (bushels/acre)',title='Winter wheat' if crop=='wheat' else crop.title())
        ax.legend(frameon=False)
    ax=axes.flat[3]
    values=[report['final_test'][k]['all_counties']['macro_mape_percent'] for k in names]
    labels=['Historical trend\n(validation winner)','History ML','History + POWER','History + POWER\n+ NLDAS']
    bars=ax.barh(labels,values,color=['#a9adb1','#8c9a9d','#267b91','#15485d'])
    for bar,v in zip(bars,values):ax.text(v+.1,bar.get_y()+bar.get_height()/2,f'{v:.2f}%',va='center')
    ax.invert_yaxis();ax.set(xlabel='Final-test crop-macro MAPE (%) — lower is better',xlim=(0,15),title='All four frozen methods, 356 test records')
    fig.suptitle('Preplant county yield forecasts: 2024–2025 Illinois test\nNASA-enriched model shown in the three scatter plots',fontsize=14)
    fig.savefig(OUT/'yield_evaluation.png',dpi=180)
    fig.savefig(OUT/'yield_evaluation.svg')
    plt.close(fig)


if __name__=='__main__':render()
