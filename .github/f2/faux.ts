import { fauxAssistantMessage, registerFauxProvider } from "@myharness/ai/compat";

export default function (pi: { registerProvider: (name: string, config: unknown) => void }) {
  const faux = registerFauxProvider({ api: "f2-faux-api", provider: "f2-faux" });
  faux.setResponses([fauxAssistantMessage("F2_FIXTURE_SUCCESS")]);
  const model = faux.getModel();
  pi.registerProvider(model.provider, {
    name: "F2 fixed fixture",
    baseUrl: model.baseUrl,
    api: model.api,
    apiKey: "fixture-only",
    models: [{
      id: model.id,
      name: model.name,
      reasoning: model.reasoning,
      input: model.input,
      cost: model.cost,
      contextWindow: model.contextWindow,
      maxTokens: model.maxTokens,
    }],
  });
}
