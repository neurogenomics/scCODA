"""
Models for the model comparison part in the paper

These models are otherwise not part of SCDCdm

:authors: Johannes Ostner
"""
import numpy as np
import pandas as pd
import subprocess as sp

import tensorflow as tf
import tensorflow_probability as tfp
from tensorflow_probability.python.experimental import edward2 as ed

import statsmodels as sm
from statsmodels.formula.api import glm
from scipy import stats

from scdcdm.util import result_classes as res
from scdcdm.model import dirichlet_models as dm

tfd = tfp.distributions
tfb = tfp.bijectors

import os

class SimpleModel(dm.CompositionalModel):
    """
    Simple Dirichlet-Multinomial model with normal priors

    """

    def __init__(self, *args, **kwargs):

        super(self.__class__, self).__init__(*args, **kwargs)
        dtype = tf.float32

        # All parameters that are returned for analysis
        self.param_names = ["alpha", "beta", "concentration", "prediction"]

        # Model definition
        def define_model(x, n_total, K):
            N, D = x.shape
            dtype = tf.float32

            alpha = ed.Normal(loc=tf.zeros([K], dtype=dtype), scale=tf.ones([K], dtype=dtype), name="alpha")
            beta = ed.Normal(loc=tf.zeros([D, K], dtype=dtype), scale=tf.ones([D,K], dtype=dtype), name="beta")

            concentration_ = tf.exp(alpha + tf.matmul(x, beta))

            # Likelihood
            predictions = ed.DirichletMultinomial(n_total, concentration=concentration_, name="predictions")
            return predictions

        # Joint posterior distribution
        self.log_joint = ed.make_log_joint_fn(define_model)
        # Function to compute log posterior probability

        self.target_log_prob_fn = lambda alpha_, beta_: self.log_joint(x=self.x,
                                                                       n_total=self.n_total,
                                                                       K=self.K,
                                                                       predictions=self.y,
                                                                       alpha=alpha_,
                                                                       beta=beta_,
                                                                       )

        alpha_size = [self.K]
        beta_size = [self.D, self.K]

        self.params = [tf.random.uniform(minval=-3, maxval=3, name="alpha", shape=alpha_size, dtype=dtype),
                       tf.random.uniform(minval=-2, maxval=2, name="beta", shape=beta_size, dtype=dtype),
                       ]

    def sample_hmc(self, num_results=int(20e3), n_burnin=int(5e3), num_leapfrog_steps=10, step_size=0.01):
        """
        HMC sampling

        Parameters
        ----------
        num_results -- int
            MCMC chain length (default 20000)
        n_burnin -- int
            Number of burnin iterations (default 5000)
        num_leapfrog_steps -- int
            HMC leapfrog steps (default 10)
        step_size -- float
            Initial step size (default 0.01)

        Returns
        -------
        result
            scdcdm.util.result_data object
        """

        # (not in use atm)
        constraining_bijectors = [
            tfb.Identity(),
            tfb.Identity(),
        ]

        # HMC transition kernel
        hmc_kernel = tfp.mcmc.HamiltonianMonteCarlo(
            target_log_prob_fn=self.target_log_prob_fn,
            step_size=step_size,
            num_leapfrog_steps=num_leapfrog_steps)
        hmc_kernel = tfp.mcmc.TransformedTransitionKernel(
            inner_kernel=hmc_kernel, bijector=constraining_bijectors)
        hmc_kernel = tfp.mcmc.SimpleStepSizeAdaptation(
            inner_kernel=hmc_kernel, num_adaptation_steps=int(4000), target_accept_prob=0.8)

        # HMC sampling
        states, kernel_results = self.sampling(num_results, n_burnin, hmc_kernel, self.params)
        states_burnin = self.get_chains_after_burnin(states, kernel_results, n_burnin)

        y_hat = self.get_y_hat(states_burnin, num_results, n_burnin)

        params = dict(zip(self.param_names, states_burnin))

        posterior = {var_name: [var] for var_name, var in params.items() if
                     "prediction" not in var_name}
        posterior_predictive = {"prediction": [params["prediction"]]}
        observed_data = {"y": self.y}
        dims = {"alpha": ["cell_type"],
                "beta": ["covariate", "cell_type"],
                "concentration": ["sample", "cell_type"],
                "prediction": ["sample", "cell_type"]
                }
        coords = {"cell_type": self.cell_types,
                  "covariate": self.covariate_names,
                  "sample": range(self.y.shape[0])
                  }

        return res.CAResultConverter(posterior=posterior,
                                     posterior_predictive=posterior_predictive,
                                     observed_data=observed_data,
                                     dims=dims,
                                     coords=coords).to_result_data(y_hat, baseline=False)

    # Calculate predicted cell counts (for analysis purposes)
    def get_y_hat(self, states_burnin, num_results, n_burnin):
        """
        Calculate predicted cell counts (for analysis purposes) and add intermediate parameters to MCMC results

        Parameters
        ----------
        states_burnin -- list
            MCMC chain without burnin samples
        num_results -- int
            Chain length (with burnin)
        n_burnin -- int
            Number of burnin samples

        Returns
        -------
        y_mean
            predicted cell counts
        """

        chain_size_y = [num_results - n_burnin, self.N, self.K]

        alphas = states_burnin[0]
        alphas_final = alphas.mean(axis=0)

        betas = states_burnin[1]
        betas_final = betas.mean(axis=0)

        conc_ = np.exp(np.einsum("jk, ...kl->...jl", self.x, betas)
                       + alphas.reshape((num_results - n_burnin, 1, self.K)))

        predictions_ = np.zeros(chain_size_y)
        for i in range(num_results - n_burnin):
            pred = tfd.DirichletMultinomial(self.n_total, conc_[i, :, :]).mean().numpy()
            predictions_[i, :, :] = pred

        states_burnin.append(conc_)
        states_burnin.append(predictions_)

        concentration = np.exp(np.matmul(self.x, betas_final) + alphas_final).astype(np.float32)
        y_mean = concentration / np.sum(concentration, axis=1, keepdims=True) * self.n_total.numpy()[:, np.newaxis]
        return y_mean


class PoissonModel:
    """
    Implements the Poisson regression model from Haber et al. into the scdcdm framework
    (for model comparison purposes)
    """

    def __init__(self, covariate_matrix, data_matrix):
        """
        Constructor of model class

        Parameters
        ----------
        covariate_matrix -- numpy array [NxD]
            covariate matrix
        data_matrix -- numpy array [NxK]
            cell count matrix
        cell_types -- list
            list of cell type names
        covariate_names -- list
            List of covariate names
        """

        self.x = covariate_matrix
        self.y = data_matrix
        self.n_total = np.sum(data_matrix, axis=1)

        self.p_val = {}

        # Get dimensions of data
        self.N, self.D = self.x.shape
        self.K = self.y.shape[1]

        # Check input data
        if self.N != self.y.shape[0]:
            raise ValueError("Wrong input dimensions X[{},:] != y[{},:]".format(self.x.shape[0], self.y.shape[0]))
        if self.N != len(self.n_total):
            raise ValueError("Wrong input dimensions X[{},:] != n_total[{}]".format(self.x.shape[0], len(self.n_total)))

    def fit_model(self):
        """
        Fits Poisson model

        Returns
        -------

        """

        for k in range(self.K):
            data_ct = pd.DataFrame({"x": self.x[:, 0],
                                    "y": self.y[:, k]})

            model_ct = glm('y ~ x', data=data_ct, family=sm.api.families.Poisson(), offset=np.log(self.n_total)).fit()
            self.p_val[k] = model_ct.pvalues[1]

    def eval_model(self):
        """
        Evaluates Poisson model.
        It is assumed that the effect on the first cell type is significant, all others are not.

        Returns
        -------
        tp, tn, fp, fn : Tuple
            Number of True positive, ... effects
        """

        ks = list(range(self.K))[1:]

        tp = sum([self.p_val[0] < 1e-10])
        fn = sum([self.p_val[0] >= 1e-10])
        tn = sum([self.p_val[k] >= 1e-10 for k in ks])
        fp = sum([self.p_val[k] < 1e-10 for k in ks])

        return tp, tn, fp, fn


class scdney_model:
    """
    wrapper for using the scdney package for R with scdcdm data
    """

    def __init__(self, data, ns):
        """
        Prepares R sampling

        Parameters
        ----------
        data -- scdcdm data object
            scdcdm data object
        ns -- list
            number of samples per condition

        Returns
        -------
        Creates .txt objects
        """

        # prepare list generation
        k = data.X.shape[1]
        x_vec = data.X.flatten()
        cell_types = ["cell_" + x for x in data.var.index.tolist()]
        cell_types[0] = "cell_" + str(k)
        conditions = ["Cond_0", "Cond_1"]

        subjects = []
        for n in range(ns[0]):
            subjects.append("Cond_0_sub_" + str(n))
        for n in range(ns[1]):
            subjects.append("Cond_1_sub_" + str(n))

        # produce lists to use in scdney
        scdc_celltypes = []
        scdc_subject = []
        scdc_cond = []
        scdc_sample_cond = []

        for i in range(len(x_vec)):
            current_count = x_vec[i]
            current_type = cell_types[i % k]
            current_subject = subjects[i // k]
            current_condition = conditions[i // (k * ns[0])]

            scdc_sample_cond.append(current_condition)

            for j in range(int(current_count)):
                scdc_celltypes.append(current_type)
                scdc_subject.append(current_subject)
                scdc_cond.append(current_condition)

        # save lists as csv
        path = "/home/icb/johannes.ostner/compositional_diff/compositionalDiff-johannes_tests_2/"
        # path = ""

        with open(path + "paper_simulation_scripts/scdc_r_data/scdc_cellTypes.txt", "w") as f:
            for c in scdc_celltypes:
                f.write(str(c) + "\n")
        with open(path + "paper_simulation_scripts/scdc_r_data/scdc_subject.txt", "w") as f:
            for c in scdc_subject:
                f.write(str(c) + "\n")
        with open(path + "paper_simulation_scripts/scdc_r_data/scdc_condition.txt", "w") as f:
            for c in scdc_cond:
                f.write(str(c) + "\n")
        with open(path + "paper_simulation_scripts/scdc_r_data/scdc_short_conditions.txt", "w") as f:
            for c in scdc_sample_cond:
                f.write(str(c) + "\n")

    def analyze(self):
        """
        Analyzes results from R script for SCDC from scdney packege.
        It is assumed that the effect on the first cell type is significant, all others are not.

        Returns
        -------
        Tuple:
            Tuple(raw summary from R, True positive...)
        """
        server = True

        if server:
            rscript = "/home/icb/johannes.ostner/anaconda3/lib/R/bin/Rscript"
            path = "/home/icb/johannes.ostner/compositional_diff/compositionalDiff-johannes_tests_2/"

        else:
            rscript = 'C:/Program Files/R/R-3.6.3/bin/Rscript'
            path = ""

        sp.call([rscript, path + 'paper_simulation_scripts/scdc_r_data/scdney_server_script.R'])

        # read-in results
        with open(path + "paper_simulation_scripts/scdc_r_data/scdc_summary.csv", "r") as f:
            r_summary = pd.read_csv(f, header=0, index_col=1)

        p_values = r_summary.loc[r_summary.index.str.contains("condCond_1"), "p.value"].values

        tp = np.sum(p_values[-1] < 0.05)
        fn = np.sum(p_values[-1] >= 0.05)
        tn = np.sum(p_values[:-1] >= 0.05)
        fp = np.sum(p_values[:-1] < 0.05)

        return r_summary, (tp, tn, fp, fn)


def make_clr_model(formula, data, *args, **kwargs):
    """
    Performs a CLR transform before outputting a statsmodels.glm model

    Usage:
    m = make_clr_model(formula="x_0 ~ c_1+c_2", data=data)
    m_s = m.fit()
    print(m_s.summary())

    Parameters
    ----------
    data -- scdcdm data object
        The data object
    formula -- string
        The model formula for the design matrix
    args, kwargs -- For use in statsmodels.GLM

    Returns
    -------
    a statsmodels.GLM object
    """

    n_total = np.sum(data.X, axis=1)

    # Get data from data object
    y = data.obs

    # Get dimensions of data
    N, D = data.X.shape

    # Check input data
    if N != data.obs.shape[0]:
        raise ValueError("Wrong input dimensions X[{},:] != y[{},:]".format(data.X.shape[0], data.obs.shape[0]))
    if N != len(n_total):
        raise ValueError("Wrong input dimensions X[{},:] != n_total[{}]".format(data.X.shape[0], len(n_total)))

    # computes clr-transformed data matrix as a pandas DataFrame
    geom_mean = np.prod(data.X, axis=1, keepdims=True)**(1/D)
    x_clr = pd.DataFrame(np.log(data.X/geom_mean), columns=data.var.index, index=y.index)

    return sm.api.GLM.from_formula(formula=formula,
                               data=pd.concat([x_clr, y], axis=1),
                               *args, **kwargs)


#%%

class FrequentistModel:
    """
    Superclass for making frequentist models in statsmodels from scdcdm data (for model comparison)
    """

    def __init__(self, data):

        x = data.obs.to_numpy()
        y = data.X
        self.x = x
        self.y = y
        self.n_total = np.sum(y, axis=1)

        self.p_val = {}

        # Get dimensions of data
        N = y.shape[0]

        # Check input data
        if N != x.shape[0]:
            raise ValueError("Wrong input dimensions X[{},:] != y[{},:]".format(y.shape[0], x.shape[0]))
        if N != len(self.n_total):
            raise ValueError("Wrong input dimensions X[{},:] != n_total[{}]".format(y.shape[0], len(self.n_total)))

    def eval_model(self, alpha=0.05, fdr_correct=True):
        """
        Evaluates array of p-values.
        It is assumed that the effect on the first cell type is significant, all others are not.

        Returns
        -------
        tp, tn, fp, fn : Tuple
            Number of True positive, ... effects
        """

        K = self.y.shape[1]
        ks = list(range(K))[1:]

        if fdr_correct:
            reject, pvals, _, _ = sm.stats.multitest.multipletests(self.p_val, alpha, method="fdr_bh")
            tp = sum([reject[0] == True])
            fn = sum([reject[0] == False])
            tn = sum([reject[k] == False for k in ks])
            fp = sum([reject[k] == True for k in ks])
        else:
            tp = sum([self.p_val[0] < alpha])
            fn = sum([self.p_val[0] >= alpha])
            tn = sum([self.p_val[k] >= alpha for k in ks])
            fp = sum([self.p_val[k] < alpha for k in ks])

        return tp, tn, fp, fn


class HaberModel(FrequentistModel):
    """
    Implements the Poisson regression model from Haber et al. into the scdcdm framework
    (for model comparison purposes)
    """
    def fit_model(self):
        """
        Fits Poisson model

        Returns
        -------
        p_val : list
            p-values for differential abundance test of all cell types
        """

        p_val = []
        K = self.y.shape[1]

        if self.y.shape[0] == 2:
            p_val = [0 for x in range(K)]
        else:
            for k in range(K):
                data_ct = pd.DataFrame({"x": self.x[:, 0],
                                        "y": self.y[:, k]})

                model_ct = glm('y ~ x', data=data_ct, family=sm.genmod.families.Poisson(), offset=np.log(self.n_total)).fit()
                p_val.append(model_ct.pvalues[1])

        self.p_val = p_val


class CLRModel(FrequentistModel):
    """
    Implements a CLR transform and subsequent linear model on each cell type into the scdcdm framework
    (for model comparison purposes)
    """
    def fit_model(self):
        """
        Fits CLR model

        Returns
        -------
        p_val : list
            p-values for differential abundance test of all cell types
        """

        p_val = []
        K = self.y.shape[1]
        D = self.x.shape[1]

        if self.y.shape[0] == 2:
            p_val = [0 for x in range(K)]
        else:
            # computes clr-transformed data matrix as a pandas DataFrame
            geom_mean = np.prod(self.y, axis=1, keepdims=True) ** (1 / K)
            y_clr = np.log(self.y / geom_mean)

            for k in range(K):
                data_ct = pd.DataFrame({"x": self.x[:, 0],
                                        "y": y_clr[:, k]})

                model_ct = glm('y ~ x', data=data_ct).fit()
                p_val.append(model_ct.pvalues[1])

        self.p_val = p_val


class TTest(FrequentistModel):
    """
        Implements a KS test one each cell type into the scdcdm framework
        (for model comparison purposes)
    """

    def fit_model(self):
        """
        Fits KS test model

        Returns
        -------
        p_val : list
            p-values for differential abundance test of all cell types
        """

        p_val = []
        N, K = self.y.shape

        n_group = int(N/2)

        if self.y.shape[0] == 2:
            p_val = [0 for x in range(K)]
        else:
            for k in range(K):

                test = stats.ttest_ind(self.y[0:n_group, k], self.y[n_group:, k])
                p_val.append(test[1])

        self.p_val = p_val


class CLR_ttest(FrequentistModel):
    """
    Implements a CLR transform and subsequent linear model on each cell type into the scdcdm framework
    (for model comparison purposes)
    """
    def fit_model(self):
        """
        Fits CLR model

        Returns
        -------
        p_val : list
            p-values for differential abundance test of all cell types
        """

        p_val = []
        N, K = self.y.shape
        D = self.x.shape[1]

        n_group = int(N/2)

        if self.y.shape[0] == 2:
            p_val = [0 for x in range(K)]
        else:
            # computes clr-transformed data matrix as a pandas DataFrame
            geom_mean = np.prod(self.y, axis=1, keepdims=True) ** (1 / K)
            y_clr = np.log(self.y / geom_mean)

            for k in range(K):
                test = stats.ttest_ind(y_clr[0:n_group, k], y_clr[n_group:, k])
                p_val.append(test[1])

        self.p_val = p_val


class ALDEx2Model(FrequentistModel):

    def fit_model(self, method="we.eBH", server=False, *args, **kwargs):

        if server:
            os.environ["R_HOME"] = "/home/icb/johannes.ostner/anaconda3/lib/R"
            os.environ["PATH"] = r"/home/icb/johannes.ostner/anaconda3/lib/R/bin" + ";" + os.environ["PATH"]
        else:
            os.environ["R_HOME"] = "C:\\Program Files\\R\\R-4.0.2"
            os.environ["PATH"] = r"C:\\Program Files\\R\\R-4.0.2\\bin\\x64" + ";" + os.environ["PATH"]

        K = self.y.shape[1]

        if self.y.shape[0] == 2:
            p_val = [0 for x in range(K)]
            self.result = None
        else:

            import rpy2.robjects as rp
            from rpy2.robjects import numpy2ri, pandas2ri
            numpy2ri.activate()
            pandas2ri.activate()
            import rpy2.robjects.packages as rpackages
            aldex2 = rpackages.importr("ALDEx2")

            cond = rp.vectors.FloatVector(self.x.astype("str").flatten().tolist())

            X_t = self.y.T
            nr, nc = X_t.shape
            X_r = rp.r.matrix(X_t, nrow=nr, ncol=nc)

            aldex_out = aldex2.aldex(X_r, cond, *args, **kwargs)
            aldex_out = pd.DataFrame(aldex_out)

            p_val = aldex_out.loc[:, method]

            self.result = aldex_out

        self.p_val = p_val


