"""Metadata self-consistency guards.

These assertions are the safety net for "adding a provider type and
forgetting one of the places it must be registered".
"""
from ai_plan_insight import provider_types
from ai_plan_insight.instance_config import V2InstanceConfig
from ai_plan_insight.provider_registry import (
    _FETCH_FACTORIES, _PUSH_CONVERTERS, _PUSH_REQUEST_MODELS,
)

FETCH_TYPES = {n for n, t in provider_types.PROVIDER_TYPES.items() if "fetch" in t.modes}
PUSH_TYPES = {n for n, t in provider_types.PROVIDER_TYPES.items() if "push" in t.modes}


class TestRegistryCoverage:
    def test_every_fetch_type_has_a_factory(self):
        assert FETCH_TYPES <= set(_FETCH_FACTORIES)

    def test_every_push_type_has_a_request_model(self):
        assert PUSH_TYPES <= set(_PUSH_REQUEST_MODELS)

    def test_every_push_type_has_a_converter(self):
        assert PUSH_TYPES <= set(_PUSH_CONVERTERS)

    def test_every_factory_belongs_to_a_fetch_type(self):
        assert set(_FETCH_FACTORIES) == FETCH_TYPES

    def test_every_request_model_belongs_to_a_push_type(self):
        assert set(_PUSH_REQUEST_MODELS) == PUSH_TYPES

    def test_every_converter_belongs_to_a_push_type(self):
        assert set(_PUSH_CONVERTERS) == PUSH_TYPES


class TestFieldMetadata:
    def test_required_in_is_subset_of_modes(self):
        for t in provider_types.PROVIDER_TYPES.values():
            for f in t.fields:
                assert f.required_in <= t.modes, f"{t.name}.{f.name}"

    def test_field_names_exist_on_instance_config(self):
        known = set(V2InstanceConfig.model_fields)
        for t in provider_types.PROVIDER_TYPES.values():
            for f in t.fields:
                assert f.name in known, f"{t.name}.{f.name}"

    def test_field_names_are_not_structural(self):
        structural = {"type", "mode", "label", "order"}
        for t in provider_types.PROVIDER_TYPES.values():
            for f in t.fields:
                assert f.name not in structural, f"{t.name}.{f.name}"

    def test_display_names_and_labels_are_non_empty(self):
        for name, t in provider_types.PROVIDER_TYPES.items():
            assert t.name == name
            assert t.display_name.strip()
            assert t.modes <= {"fetch", "push"} and t.modes
            for f in t.fields:
                assert f.label.strip()

    def test_no_duplicate_field_names_within_a_type(self):
        for t in provider_types.PROVIDER_TYPES.values():
            names = [f.name for f in t.fields]
            assert len(names) == len(set(names)), t.name


class TestDerivations:
    def test_supported_types_matches_modes(self):
        assert provider_types.supported_types()["antigravity"] == {"fetch", "push"}
        assert provider_types.supported_types()["claude"] == {"push"}

    def test_allowed_fields_lists_every_field(self):
        allowed = provider_types.allowed_credential_fields()
        assert allowed["huawei_cloud"] == {"user_name", "access_key_id", "access_key_secret"}
        assert allowed["claude"] == set()

    def test_required_fields_keyed_by_type_and_mode(self):
        required = provider_types.required_credential_fields()
        assert required[("kimi", "fetch")] == {"api_key"}
        assert required[("antigravity", "fetch")] == {"api_key"}
        assert ("antigravity", "push") not in required

    def test_display_name_falls_back_to_raw_name(self):
        assert provider_types.display_name("kimi") == "Kimi Coding Plan"
        assert provider_types.display_name("nonexistent") == "nonexistent"

    def test_to_json_shape(self):
        entries = {e["name"]: e for e in provider_types.to_json()}
        assert entries["kimi"]["modes"] == ["fetch"]
        api_key = entries["kimi"]["fields"][0]
        assert api_key["name"] == "api_key"
        assert api_key["required_in"] == ["fetch"]
        assert api_key["secret"] is True
        assert isinstance(api_key["help"], str)
