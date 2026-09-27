"""
Tests for adapters/registry.py — the provider set as data, not as branches.

LangChain is the one provider now (see adapters/registry.py's module
docstring) — the registry pattern itself is kept as a single-entry seam, not
because multiple providers exist today.

Construction here is credential-free by design: `make_langchain_adapter`
defers its API-key and connection checks to call time, so the provider can be
built in a network-free suite. The tests assert that property explicitly, so
if the adapter ever moved its credential check into construction, this suite
would start requiring a live key to run and the failure would look like an
unrelated environment problem.
"""

import unittest

from tests.support import make_scripted_adapter
from adapters.registry import (
    PROVIDERS,
    ProviderSpec,
    build_adapter,
    model_label,
    provider_names,
    resolve,
    with_model_override,
)


class TestProviderSet(unittest.TestCase):
    def test_every_registered_provider_builds_without_credentials(self):
        for name in provider_names():
            with self.subTest(provider=name):
                adapter = build_adapter({"provider": name})
                self.assertTrue(callable(adapter))

    def test_unknown_provider_names_the_valid_set(self):
        """The error has to be actionable — a bare KeyError is not."""
        with self.assertRaises(ValueError) as ctx:
            resolve("not-a-provider")
        message = str(ctx.exception)
        for name in provider_names():
            self.assertIn(name, message)

    def test_every_spec_is_internally_consistent(self):
        for name, spec in PROVIDERS.items():
            with self.subTest(provider=name):
                self.assertEqual(spec.name, name, "registry key must match spec.name")
                # A provider either has both a model key and a default, or neither.
                self.assertEqual(
                    spec.model_key is None,
                    spec.default_model is None,
                    "model_key and default_model must be declared together",
                )

    def test_supports_tools_is_callable_not_a_fixed_bool(self):
        """
        Repurposed once there was only one provider: not "does this provider
        have a tool-calling capability" (always true) but "is that capability
        actually usable right now" — depends on live TAVILY_API_KEY presence,
        which is why it's a callable rather than a value fixed at registration.
        """
        spec = resolve("langchain")
        self.assertTrue(callable(spec.supports_tools))
        self.assertIsInstance(spec.supports_tools(), bool)


class TestModelLabel(unittest.TestCase):
    def test_label_names_the_configured_model(self):
        self.assertEqual(
            model_label({"provider": "langchain", "model": "gemini-2.5-flash-lite"}),
            "langchain:gemini-2.5-flash-lite",
        )
        self.assertEqual(
            model_label({"provider": "langchain", "model": "qwen2.5:7b"}),
            "langchain:qwen2.5:7b",
        )

    def test_label_falls_back_to_the_declared_default(self):
        for name, spec in PROVIDERS.items():
            if spec.default_model is None:
                continue
            with self.subTest(provider=name):
                self.assertIn(spec.default_model, model_label({"provider": name}))


class TestModelOverride(unittest.TestCase):
    def test_model_lands_on_the_key_the_provider_reads(self):
        self.assertEqual(
            with_model_override({"provider": "langchain"}, "qwen2.5:7b"),
            {"provider": "langchain", "model": "qwen2.5:7b"},
        )

    def test_model_override_is_ignored_for_a_provider_with_no_model(self):
        """
        The specific bug this prevents: writing a model name to a key nothing reads,
        which produces a config that looks overridden and behaves as if it wasn't.
        The shipped "langchain" provider always has a model_key, so this
        registers a throwaway provider with model_key=None just to exercise
        that branch.
        """
        PROVIDERS["test-no-model"] = ProviderSpec(
            name="test-no-model",
            model_key=None,
            default_model=None,
            build=lambda cfg: make_scripted_adapter("none"),
            describe=lambda cfg: "test-no-model",
        )
        try:
            self.assertEqual(
                with_model_override({"provider": "test-no-model"}, "gemini-2.5-flash"),
                {"provider": "test-no-model"},
            )
        finally:
            PROVIDERS.pop("test-no-model", None)

    def test_original_config_is_not_mutated(self):
        base = {"provider": "langchain", "model": "gemini-2.5-flash"}
        with_model_override(base, "llama3.2")
        self.assertEqual(base, {"provider": "langchain", "model": "gemini-2.5-flash"})


class TestOpenForExtension(unittest.TestCase):
    """Registering a provider must not require editing any caller."""

    def setUp(self):
        self._added = "test-echo"
        PROVIDERS[self._added] = ProviderSpec(
            name=self._added,
            model_key="echo_model",
            default_model="echo-1",
            build=lambda cfg: make_scripted_adapter("none"),
            describe=lambda cfg: f"echo:{cfg.get('echo_model') or 'echo-1'}",
        )

    def tearDown(self):
        PROVIDERS.pop(self._added, None)

    def test_new_provider_is_immediately_resolvable_buildable_and_labelled(self):
        self.assertIn(self._added, provider_names())
        self.assertTrue(callable(build_adapter({"provider": self._added})))
        self.assertEqual(model_label({"provider": self._added}), "echo:echo-1")
        self.assertEqual(
            with_model_override({"provider": self._added}, "echo-2")["echo_model"],
            "echo-2",
        )


if __name__ == "__main__":
    unittest.main()
