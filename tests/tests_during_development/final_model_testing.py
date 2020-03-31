""""
This file contains simple simulation tests that are used to see if all parts of the package work

:authors: Johannes Ostner

"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import importlib
import arviz as az

from scdcdm.util import compositional_analysis_generation_toolbox as gen
from scdcdm.util import comp_ana as mod
from scdcdm.util import result_classes as res
from scdcdm.util import multi_parameter_sampling as mult

pd.options.display.float_format = '{:10,.3f}'.format
pd.set_option('display.max_columns', None)

#%%
# Artificial data
np.random.seed(1234)

n = 3

cases = 1
K = 5
n_samples = [n, n]
n_total = np.full(shape=[2*n], fill_value=1000)

data = gen.generate_case_control(cases, K, n_total[0], n_samples,
                                 w_true=np.array([[1, 0, 0, 0, 0]]),
                                 b_true=np.log(np.repeat(0.2, K)).tolist())

print(data.uns["w_true"])
print(data.uns["b_true"])

print(data.X)
print(data.obs)

#%%

n = 3

cases = 2
K = 5
n_samples = [n, n, n, n]
n_total = np.full(shape=[4*n], fill_value=1000)

data = gen.generate_case_control(cases, K, n_total[0], n_samples,
                                 w_true=np.array([[1, 0, 0, 0, 0], [0, 1, 0, 0, 0]]),
                                 b_true=np.log(np.repeat(0.2, K)).tolist())

print(data.uns["w_true"])
print(data.uns["b_true"])

print(data.X)
print(data.obs)
print(data.var)

#%%
importlib.reload(mod)
importlib.reload(res)

#data.obs["x_0"] = ["A", "A", "A", "A", "A", "A", "B", "B", "B", "B", "B", "B"]
#data.obs["x_1"] = ["A", "A", "A", "B", "B", "B", "A", "A", "A", "B", "B", "B"]

ana = mod.CompositionalAnalysis(data, "x_0", baseline_index=None)
print(ana.x)
print(ana.covariate_names)

#%%
params_mcmc = ana.sample_hmc(num_results=int(20000), n_burnin=5000)

#%%
params_mcmc.summary(credible_interval=0.9)

#%%
az.plot_trace(params_mcmc)
plt.show()




#%%
importlib.reload(mod)
importlib.reload(res)

#data.obs["x_0"] = ["A", "A", "A", "A", "A", "A", "B", "B", "B", "B", "B", "B"]
#data.obs["x_1"] = ["A", "A", "A", "B", "B", "B", "A", "A", "A", "B", "B", "B"]

ana_simple = mod.CompositionalAnalysis(data, "x_0", baseline_index="simple")
print(ana_simple.x)
print(ana_simple.covariate_names)

#%%
params_mcmc = ana_simple.sample_hmc(num_results=int(2000), n_burnin=500)

#%%
params_mcmc.summary(credible_interval=0.95)

#%%
az.plot_trace(params_mcmc)
plt.show()

#%%

params_mcmc.traceplots()


#%%
importlib.reload(mod)

params_nuts = model.sample_nuts(num_results=int(1e4), n_burnin=5000)
print(params_nuts)

#%%

model_2 = mod.CompositionalAnalysis(data, "x_0", baseline_index=K)

params_mcmc_2 = model_2.sample_hmc(n_burnin=5000, num_results=int(2e4))
print(params_mcmc_2)

#%%
params_mcmc.plot()

#%%
params_mcmc_2.plot()

#%%

# Haber Data

data_path = "C:/Users/Johannes/Documents/Uni/Master's_Thesis/data/haber_atlas_metadata.txt"
data = pd.read_csv(data_path, sep="\t", header=[0], skiprows=[1])
mice = data.groupby(["Mouse", "Cluster"])\
             .agg({"Cluster": "count"})\
             .rename(columns={"Cluster": "Count"})['Count']\
             .unstack(fill_value=0)\
             .rename(index={"H.poly_Day10_1": "H.poly.Day10_1",
                            "H.poly_Day10_2": "H.poly.Day10_2",
                            "H.poly_Day3_1": "H.poly.Day3_1",
                            "H.poly_Day3_2": "H.poly.Day3_2"})
mice_sum = mice.groupby([s.split('_')[0] for s in mice.index.values]).sum()\
                   .loc[["Control", "H.poly.Day3", "H.poly.Day10", "Salm"], :]
mice_sum = mice_sum.loc[:, (mice_sum != 0).any(axis=0)].div(mice_sum.sum(axis=1), axis=0)

mice_plot = pd.melt(mice_sum.reset_index(), id_vars="index", var_name="Cell_Type", value_name="Count")

#%%
salm = mice[mice.index.str.contains("^(?:Control|Salm)")]
salm = salm.loc[:, (salm != 0).any(axis=0)]
print(salm)

x = np.array([[0], [0], [0], [0], [1], [1]])

n_total = salm.sum(axis=1).values

#%%
fig, ax = plt.subplots(figsize=(12, 5))
sns.barplot(data=mice_plot, x="Cell_Type", y="Count", hue="index", ax=ax)
plt.show()

#%%

model_salm = mod.compositional_model_baseline(x, salm.values, n_total, baseline_index=3)
salm_mcmc = model_salm.sample_hmc(n_burnin=5000, num_results=int(2e4))
print(salm_mcmc)
#%%
model_salm_2 = mod.compositional_model_no_baseline(x, salm.values, n_total)
salm_mcmc_2 = model_salm_2.sample_hmc(n_burnin=5000, num_results=int(2e4))
print(salm_mcmc_2)


#%%
salm_mcmc.plot()

#%%

cases = [1]
K = [3]
n_samples = [[5, 5] for i in range(1)]
n_total = [1000]
b_true = [np.log(np.repeat(0.2, 3)).tolist(),
          ]
w_true = []
for x in [1]:
    w_true.append(np.array([[1, 0, 0]]))
num_results = [5e3+100]

p = mult.Multi_param_simulation(cases, K, n_total, n_samples, b_true, w_true, num_results,
                                model=mod.compositional_model_no_baseline)

p_2 = mult.Multi_param_simulation(cases, K, n_total, n_samples, b_true, w_true, num_results,
                                  model=mod.compositional_model_baseline)

#%%

p.simulate(keep_raw_params=False)
#%%

p_2.simulate(keep_raw_params=False)

#%%
print(p.mcmc_results)


#%%
for r in p_2.mcmc_results.values():
    r.plot()

#%%
p.get_discovery_rates()

#%%

cases = [1]
K = [5]
# n_samples = [[i+1,j+1] for i in range(10) for j in range(10)]
n_samples = [[2, 2], [5, 5], [10, 10]]
# n_total = [1000]
n_total = [1000]
num_results = [20e3]


b_true = [np.log(np.array([0.2, 0.2, 0.2, 0.2, 0.2])).tolist()]

w_true = []
for x in [0.3, 0.5, 1]:
    w_true.append(np.array([[x, 0, 0, 0, 0]]).tolist())

#%%

p = mult.Multi_param_simulation_multi_model(cases, K, n_total, n_samples, b_true, w_true, num_results,
                                            models=[mod.compositional_model_no_baseline, mod.compositional_model_no_baseline])

p.simulate()

#%%

for i in range(9):
    print(p.parameters.iloc[i])
    print((p.results[0][i]).y)
    for m in p.results.keys():
        print(p.results[m][i])



#%%
#einsum tests

a = np.arange(10).reshape((10, 1))
b = np.repeat([[[0, 1, 2, 3, 4]], [[1, 2, 3, 4, 5]]], 5, axis=0)
al = np.repeat([[0, 1, 2, 3, 4]], 10, axis=0)
print(al)
x = np.arange(6).reshape((6, 1))
print(a.shape)
print(b.shape)

print(np.matmul(x, b[6]))

#print((a.reshape((10, 1, 1))+b))
out = np.einsum("jk, ...kl->...jl", x, b)
print(al.reshape(10, 1, 5))
print((out + al.reshape(10, 1, 5)))
print(out.shape)

#%%

url = 'https://stats.idre.ucla.edu/stat/data/hsb2.csv'

hsb2 = pd.read_csv(url)


#%%

import pymc4 as pm
import numpy as np
@pm.model
def nested_model(cond):
    sd = yield pm.HalfNormal("sd", 1.)
    norm = yield pm.Normal("n", cond, sd, observed=np.random.randn(10))
    return norm

conditioned = nested_model(cond=2.)
trace = pm.sample(conditioned)

#%%

# progressbar
import progressbar as pb
import time

with pb.ProgressBar(max_value=10) as bar:
    for i in range(10):
        time.sleep(0.1)
        bar.update(i)