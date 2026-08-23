"""OpenCode Zen free-model layer for the Gold Desk (LLM infrastructure).

Architecture ported from the owner's OPENCODE_HERMES_SETUP.md (Hermes Agent):

  zen_sync.py    auto-discovery of FREE Zen models (Zen /v1/models ∩ models.dev)
  zen_client.py  keyless OpenAI-compatible client (Authorization stripped)
  veto_llm.py    the §8.3 context-veto completion (Phase-2 live; bench offline)

Laws honored:
  L3  veto output is binary ENDORSE|VETO, nothing else
  L5  timeout / bad JSON / missing model -> fail closed, never a retry-into-fill
  L10 the live bar loop calls this ONLY when constitution identity.phase >= 2;
      the veto-bench is offline research and never enters the orchestrator
  L11 no market facts from LLM memory — packs are built by the data plane
  §8.2 provider is NOT in the constitution (env/config only), per the plan
"""
