# Final campaign comparison
vqe-independent-n-01

Sample SD describes PUB variability, not independent experimental uncertainty. No confidence intervals or significance tests are computed. Pooled summaries weight n levels by n/105; prefix convergence is order-dependent. Recovery selection, backend calibration drift and account/block effects may confound comparisons. Only verified job-level QPU usage is included, when available; incomplete coverage is not compared as total cost.

All 840 accepted logical observations verified. SSE=sum(abs(Zfit-Zexp)**2); RMSE=sqrt(SSE/264).

Descriptive winners by n (median SSE):
- n=5: M7_ALL; SSE=211.36303; RMSE=0.89477233
- n=10: M4_DD_TREX; SSE=206.7428; RMSE=0.88330027
- n=15: M4_DD_TREX; SSE=394.91349; RMSE=1.2230635
- n=20: M4_DD_TREX; SSE=304.27605; RMSE=1.0735698
- n=25: M4_DD_TREX; SSE=424.39382; RMSE=1.2678929
- n=30: M1_DD; SSE=414.0877; RMSE=1.2521333
