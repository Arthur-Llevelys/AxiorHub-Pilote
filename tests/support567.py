from agent.ai_gateway import PURPOSES

def authorize_external(config):
    config['hybrid_routing']={'mode':'manual','external_client_data_approved':True,'allowed_purposes':list(PURPOSES)}
    for provider in config.get('ai_providers',{}).values():
        provider.update(monthly_budget_usd=10,per_request_budget_usd=1,input_usd_per_million=1,output_usd_per_million=1)
    return config
