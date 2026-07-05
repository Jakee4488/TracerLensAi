import logging
import importlib

import dowhy
import pandas as pd

# econml is optional in lightweight environments, so detect it dynamically.
try:
    importlib.import_module("econml.dml")
    ECONML_AVAILABLE = True
except Exception as e:
    ECONML_AVAILABLE = False
    logging.warning(
        f"econml could not be loaded due to: {e}. Causal estimation will fallback to standard methods if possible."
    )

import networkx as nx

if not hasattr(nx.algorithms, 'd_separated') and hasattr(nx, 'd_separated'):
    nx.algorithms.d_separated = nx.d_separated

logger = logging.getLogger(__name__)

class CausalOptimizer:
    """
    Core engine for identifying the causal relationships between agentic workflow steps
    and outcomes (like token cost or success rate).
    """
    def __init__(self):
        self.model = None
        self.estimate = None

    def build_causal_model(self, trace_data: list[dict], treatment: str, outcome: str, common_causes: list[str]):
        """
        Builds a Structural Causal Model (SCM) from observational trace data.
        """
        try:
            df = pd.DataFrame(trace_data)

            # Basic validation
            if treatment not in df.columns or outcome not in df.columns:
                raise ValueError(f"Missing treatment '{treatment}' or outcome '{outcome}' in trace data. Columns available: {list(df.columns)}")

            # Here we let DoWhy build the default causal graph based on common_causes.
            # In a more advanced setup, we would ingest the Graphiti context graph here.
            self.model = dowhy.CausalModel(
                data=df,
                treatment=treatment,
                outcome=outcome,
                common_causes=common_causes,
                effect_modifiers=[]
            )
            logger.info(f"Successfully built causal model for {treatment} -> {outcome}")
            return self.model
        except Exception as e:
            logger.error(f"Error building causal model: {e}")
            raise

    def estimate_treatment_effects(self) -> dict:
        """
        Estimates the CATE (Conditional Average Treatment Effect) to mathematically prove
        the impact of the treatment on the outcome.
        """
        if not self.model:
            raise RuntimeError("CausalModel not initialized. Call build_causal_model first.")

        try:
            # Step 1: Identify the causal effect
            identified_estimand = self.model.identify_effect(proceed_when_unidentifiable=True)

            # Step 2: Estimate the effect
            if ECONML_AVAILABLE:
                method_name = "backdoor.econml.dml.LinearDML"
                method_params = {
                    "init_params": {"random_state": 42},
                    "fit_params": {}
                }
            else:
                # Fallback to a simpler linear regression if econml is missing
                method_name = "backdoor.linear_regression"
                method_params = {}

            self.estimate = self.model.estimate_effect(
                identified_estimand,
                method_name=method_name,
                target_units="ate",
                method_params=method_params
            )

            effect_value = float(self.estimate.value)

            return {
                "estimated_effect": effect_value,
                "treatment_name": self.model._treatment[0],
                "outcome_name": self.model._outcome[0],
                "recommendation": self._generate_recommendation(effect_value, self.model._outcome[0])
            }
        except Exception as e:
            logger.error(f"Error estimating causal effects: {e}. Returning mock estimate.")
            return {
                "estimated_effect": 0.0,
                "treatment_name": self.model._treatment[0],
                "outcome_name": self.model._outcome[0],
                "recommendation": "Error calculating causal effect. Falling back to default assumption: no effect."
            }

    def _generate_recommendation(self, effect_value: float, outcome_name: str) -> str:
        """
        Generates human-readable optimization tips based on the mathematical causal effect.
        """
        if "cost" in outcome_name.lower() or "token" in outcome_name.lower():
            # For cost, positive effect is BAD. Negative effect is GOOD.
            if effect_value > 0:
                return f"Adding this feature causally INCREASES token cost by ~{effect_value:.2f}. Consider pruning."
            elif effect_value < 0:
                return f"Adding this feature causally DECREASES token cost by ~{abs(effect_value):.2f}. Keep it."
        else:
            # For success rate, positive effect is GOOD. Negative effect is BAD.
            if effect_value > 0:
                return f"This feature causally improves the outcome metric by ~{effect_value:.2f}."
            elif effect_value < 0:
                return f"This feature harms the outcome metric by ~{abs(effect_value):.2f}."

        return "This feature has a negligible causal impact on the outcome."
