from __future__ import annotations

import unittest

from scripts import public_preview


class PublicPreviewProductDetectionTestCase(unittest.TestCase):
    def test_accepts_current_config_payload_shape(self) -> None:
        self.assertTrue(
            public_preview._is_current_product_payload(
                {
                    "product_name": "Praxis AI｜无界笃行",
                    "product_feature_name": "万象成文",
                }
            )
        )

    def test_rejects_unrelated_service(self) -> None:
        self.assertFalse(
            public_preview._is_current_product_payload(
                {
                    "product_name": "Other Service",
                    "product_feature_name": "Other Feature",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
