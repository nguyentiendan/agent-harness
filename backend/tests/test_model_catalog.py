import support
import unittest
from app.billing.catalog import normalize


def model(**overrides):
    data = {
        "id": "vendor/model:free",
        "name": "Free model",
        "architecture": {"output_modalities": ["text"], "input_modalities": ["text"]},
        "supported_parameters": ["tools"],
        "context_length": 8192,
        "top_provider": {"max_completion_tokens": 4096},
        "pricing": {
            "prompt": "0",
            "completion": "0",
            "request": "0",
            "image": "0",
            "web_search": "0",
            "internal_reasoning": "0",
        },
    }
    data.update(overrides)
    return data


class CatalogTests(unittest.TestCase):
    def test_verified_free_and_paid(self):
        self.assertEqual(normalize(model())["price_class"], "free")
        item = normalize(
            model(
                id="vendor/paid",
                pricing={"prompt": ".000001", "completion": ".000002", "request": "0"},
            )
        )
        self.assertEqual(item["price_class"], "paid")
        self.assertGreater(float(item["ceiling"]), 0)

    def test_missing_negative_nonfinite_or_extra_fees_not_free(self):
        for price in (
            {"prompt": "0"},
            {"prompt": "-1", "completion": "0"},
            {"prompt": "NaN", "completion": "0"},
            {"prompt": "0", "completion": "0", "request": ".01"},
        ):
            with self.subTest(price=price):
                self.assertIsNone(normalize(model(pricing=price)))
        self.assertIsNone(normalize(model(id="vendor/zero-without-free-variant")))

    def test_unsupported_and_unbounded_rejected(self):
        for change in (
            {"supported_parameters": []},
            {"context_length": None},
            {"top_provider": {}},
            {"architecture": {"output_modalities": ["image"]}},
            {"id": "../bad"},
        ):
            self.assertIsNone(normalize(model(**change)))


class CatalogLifecycleTests(unittest.TestCase):
    def setUp(self):
        from app.platform.db import DB, CatalogModel
        from sqlalchemy import delete

        with DB.begin() as db:
            db.execute(delete(CatalogModel))

    def test_stale_refresh_failure_is_closed(self):
        from unittest.mock import patch
        from app.billing import catalog, gateway
        from app.platform.db import DB, CatalogModel

        item = normalize(model())
        with DB.begin() as db:
            db.add(
                CatalogModel(id=item["id"], payload=item, updated_at=1, present=True)
            )
        with (
            patch.dict("os.environ", OPENROUTER_ENABLED="true"),
            patch.object(catalog, "fetch", side_effect=gateway.GatewayError("offline")),
        ):
            with self.assertRaises(gateway.GatewayError):
                catalog.lookup(item["id"])

    def test_changed_classification_and_removed_model_revoke_keys(self):
        from unittest.mock import patch
        from app.billing import catalog
        from app.platform.db import DB, CatalogModel

        item = normalize(model())
        with (
            patch.dict("os.environ", OPENROUTER_ENABLED="true"),
            patch.object(catalog, "fetch", return_value=[item]),
        ):
            catalog.refresh()
            self.assertEqual(catalog.lookup(item["id"])["revision"], item["revision"])
        with (
            patch.dict("os.environ", OPENROUTER_ENABLED="true"),
            patch.object(
                catalog,
                "fetch",
                return_value=[
                    normalize(
                        model(
                            id="vendor/paid",
                            pricing={"prompt": ".001", "completion": ".001"},
                        )
                    )
                ],
            ),
        ):
            catalog.refresh()
            with DB() as db:
                self.assertFalse(db.get(CatalogModel, item["id"]).present)


class ClaudePricingTests(unittest.TestCase):
    def claude(self, **pricing):
        return model(
            id="anthropic/claude-sonnet-4.5",
            pricing={
                "prompt": "0.000003",
                "completion": "0.000015",
                "web_search": "0.01",
                "input_cache_read": "0.0000003",
                "input_cache_write": "0.00000375",
                "input_cache_write_1h": "0.000006",
                **pricing,
            },
        )

    def test_claude_optional_search_and_one_hour_cache_are_supported(self):
        item = normalize(self.claude())
        self.assertIsNotNone(item)
        self.assertEqual(item["price_class"], "paid")
        self.assertEqual(item["input_price"], "0.000006")
        self.assertEqual(item["output_price"], "0.000015")

    def test_long_context_tiers_reserve_highest_cache_and_output_rates(self):
        from decimal import Decimal

        item = normalize(
            self.claude(
                overrides=[
                    {
                        "min_prompt_tokens": 200000,
                        "prompt": "0.000006",
                        "completion": "0.0000225",
                        "input_cache_read": "0.0000006",
                        "input_cache_write": "0.0000075",
                        "input_cache_write_1h": "0.000012",
                    }
                ]
            )
        )
        self.assertIsNotNone(item)
        self.assertEqual(Decimal(item["input_price"]), Decimal("0.000012"))
        self.assertEqual(Decimal(item["output_price"]), Decimal("0.0000225"))
        self.assertEqual(
            Decimal(item["ceiling"]),
            Decimal("0.000012") * 8192 + Decimal("0.0000225") * 4096,
        )

    def test_invalid_tiers_unknown_fees_and_invalid_cache_fail_closed(self):
        for change in (
            {"overrides": {}},
            {"overrides": [{"prompt": "0.01"}]},
            {
                "overrides": [
                    {"min_prompt_tokens": 100, "prompt": "NaN", "completion": "1"}
                ]
            },
            {"input_cache_write_1h": "-1"},
            {"unknown_fee": ".01"},
        ):
            with self.subTest(change=change):
                self.assertIsNone(normalize(self.claude(**change)))

    def test_free_with_optional_charges_still_rejected(self):
        self.assertIsNone(
            normalize(
                model(pricing={"prompt": "0", "completion": "0", "web_search": ".01"})
            )
        )

    def test_batch_and_online_variants_not_chat_routes(self):
        for suffix in (":batch", ":online"):
            data = self.claude()
            data["id"] += suffix
            self.assertIsNone(normalize(data))


class GeminiPricingTests(unittest.TestCase):
    def gemini(self, **pricing):
        return model(
            id="google/gemini-2.5-pro",
            pricing={
                "prompt": "0.00000125",
                "completion": "0.00001",
                "internal_reasoning": "0.00001",
                "image": "0.00000125",
                "audio": "0.00000125",
                "input_audio_cache": "0.000000125",
                "web_search": "0.014",
                **pricing,
            },
        )

    def test_text_gemini_ignores_unused_modalities_but_bounds_reasoning(self):
        item = normalize(self.gemini(internal_reasoning="0.00002"))
        self.assertIsNotNone(item)
        self.assertEqual(item["price_class"], "paid")
        self.assertEqual(item["input_price"], "0.00000125")
        self.assertEqual(item["output_price"], "0.00002")

    def test_gemini_context_tier_includes_output_and_reasoning(self):
        item = normalize(
            self.gemini(
                overrides=[
                    {
                        "min_prompt_tokens": 200000,
                        "prompt": "0.0000025",
                        "completion": "0.000015",
                        "audio": "0.0000025",
                    }
                ]
            )
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["input_price"], "0.0000025")
        self.assertEqual(item["output_price"], "0.000015")

    def test_gemini_image_output_and_unknown_fees_stay_excluded(self):
        for data in (
            self.gemini(image_output=".01"),
            self.gemini(unknown_fee=".01"),
            self.gemini(internal_reasoning="NaN"),
            self.gemini(audio="-1"),
        ):
            self.assertIsNone(normalize(data))
        data = self.gemini()
        data["architecture"]["output_modalities"] = ["text", "image"]
        self.assertIsNone(normalize(data))

    def test_optional_fees_do_not_make_gemini_free(self):
        data = self.gemini(prompt="0", completion="0", internal_reasoning="0")
        data["id"] = "google/gemini-example:free"
        self.assertIsNone(normalize(data))
