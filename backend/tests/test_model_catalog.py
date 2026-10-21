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
