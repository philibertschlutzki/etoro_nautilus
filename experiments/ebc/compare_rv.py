import os, sys, json
os.environ["ETORO_CONFIG_DIR"] = os.path.abspath("cfg_B")
sys.path.insert(0, os.getcwd()); sys.path.insert(0, "experiments/ebc")
import numpy as np, route_r, sim
iid = sys.argv[1]
series = route_r.load_daily(iid)
S = sim.Series(*series)
mods = {"T": ("automation.strategies.ebc_trend","EbcTrendConfig","EbcTrendStrategy"),
        "M": ("automation.strategies.ebc_pullback","EbcPullbackConfig","EbcPullbackStrategy"),
        "V": ("automation.strategies.ebc_squeeze","EbcSqueezeConfig","EbcSqueezeStrategy")}
for arch,(m,cc,cl) in mods.items():
    R = route_r.run(cl,"TEST.ETORO",series,module=m,config_class=cc)
    V = sim.sequential(S, arch)
    day=86400*10**9
    rk=[(int((t["entry_ts"]-series[0][0])//day) , t) for t in R]
    ri={int(np.searchsorted(series[0], (t["entry_ts"]//day)*day)):t for t in R}
    vi={t["entry_i"]:t for t in V}
    common=set(ri)&set(vi)
    diffs=[]
    for e in common:
        a=ri[e]; b=vi[e]
        ra=(a["px_close"]/a["px_open"]-1)*1e4*(1 if a["side"]=="BUY" else -1)
        diffs.append(abs(ra-b["gross_bps"]))
    exitmis=sum(1 for e in common if int(np.searchsorted(series[0],(ri[e]["exit_ts"]//day)*day))!=vi[e]["exit_i"])
    print(arch,"R",len(R),"V",len(V),"gemeinsam",len(common),"max|dbps|",round(max(diffs),2) if diffs else None,"exit_idx_abw",exitmis)
