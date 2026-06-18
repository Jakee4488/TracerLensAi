import logging

logger = logging.getLogger(__name__)

class RoutingPolicy:
    """
    Evaluates agent outputs and intents against business rules.
    Triggers human escalation if thresholds are met.
    """
    
    HIGH_SEVERITY_KEYWORDS = ["legal", "sue", "lawyer", "cancel account", "fraud"]
    
    @classmethod
    def requires_human_escalation(cls, user_prompt: str, agent_response_content: str) -> bool:
        """
        Determines if the interaction must be routed to a human.
        """
        prompt_lower = user_prompt.lower()
        
        # 1. Keyword based heuristic
        for kw in cls.HIGH_SEVERITY_KEYWORDS:
            if kw in prompt_lower:
                logger.warning(f"Escalation triggered due to keyword: {kw}")
                return True
                
        # 2. Could also include confidence scores or sentiment analysis here
        # For example, if agent response contains specific apology formats indicating failure
        if "i am unable to help" in agent_response_content.lower():
            logger.warning("Escalation triggered due to agent inability.")
            return True
            
        return False
