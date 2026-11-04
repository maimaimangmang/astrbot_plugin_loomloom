import hashlib
import io
import json
import unittest
import zipfile
from unittest.mock import patch
import test_service  # ensures the distributable source path is set
from astrbot_plugin_loomloom.compatibility import check_listing, check_package
from astrbot_plugin_loomloom.client import BASE_URL, LoomError

# Synthetic wrapper fixture, deliberately different from the real reviewed code.
SCRIPT = 'import json\nCONFIG = json.loads(%s)\nprint("fixture")\n'
MD = 'name: fixture\ndescription: fixture\n# fixture\n- Listing ID: "example"\n- Display name snapshot: fixture\n- Description snapshot: fixture\ncloud fixture\n'
NORMAL_MD = 'name: <SNAPSHOT>\ndescription: <SNAPSHOT>\n# <SNAPSHOT>\n- Listing ID: <SNAPSHOT>\n- Display name snapshot: <SNAPSHOT>\n- Description snapshot: <SNAPSHOT>\ncloud fixture\n'
PROFILE = {'profiles':[{'script_sha256':hashlib.sha256(b'import json\nCONFIG = <PUBLIC_LISTING_CONFIG>\nprint("fixture")\n').hexdigest(),
                        'skill_sha256':hashlib.sha256(NORMAL_MD.encode()).hexdigest()}]}


def package(resource='example', base=BASE_URL, extra=False, change_script=False, change_skill=False):
    cfg = json.dumps(json.dumps({'listingId': resource, 'publicApiBaseUrl': base}))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('fixture/SKILL.md', MD + ('\nUse a local browser.' if change_skill else ''))
        z.writestr('fixture/scripts/loomloom_skill.py', SCRIPT % cfg + ('print("changed")' if change_script else ''))
        if extra: z.writestr('fixture/run.sh', 'echo custom')
    return out.getvalue()


class CompatibilityTests(unittest.TestCase):
    def test_known_cloud_wrapper_accepted(self):
        with patch('astrbot_plugin_loomloom.compatibility.Path.read_text', return_value=json.dumps(PROFILE)):
            self.assertIn('标准云端', check_package(package(), 'example'))

    def test_unknown_behavior_or_source_is_not_guessed(self):
        variants = [dict(extra=True),dict(change_script=True),dict(change_skill=True),
                    dict(resource='other'),dict(base='https://example.invalid')]
        with patch('astrbot_plugin_loomloom.compatibility.Path.read_text', return_value=json.dumps(PROFILE)):
            for args in variants:
                with self.subTest(args=args), self.assertRaises(LoomError):
                    check_package(package(**args), 'example')

    def test_platform_state_required(self):
        good = {'saleStatus':'listed','executionAvailabilityStatus':'available','listingVersionId':'v1'}
        check_listing(good)
        for field in good:
            bad=good.copy();bad.pop(field)
            with self.subTest(field=field),self.assertRaises(LoomError): check_listing(bad)

    def test_bad_zip_is_readable_error(self):
        with self.assertRaises(LoomError): check_package(b'invalid', 'example')
