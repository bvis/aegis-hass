# Quality scale

Home Assistant rates integrations against its [integration quality scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/) (bronze, silver, gold, platinum). The scale is only enforced for core integrations; this one tracks it anyway as a checklist.

The current status of every rule is in [`custom_components/aegis_ajax/quality_scale.yaml`](../custom_components/aegis_ajax/quality_scale.yaml):

- `done`: the rule is met.
- `todo`: not met yet; the comment links the issue tracking it. All of them carry the [`quality-scale`](https://github.com/bvis/aegis-hass/issues?q=label%3Aquality-scale) label.
- `exempt`: the rule doesn't apply or the integration deliberately does something else; the comment says why.

When a change fixes a `todo` rule, update the file in the same PR.
