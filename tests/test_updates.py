import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from codex_provider_launcher import updates


class UpdateTests(unittest.TestCase):
    def release(self):
        name=updates.asset_name()
        return {'tag_name':'v9.0.0','draft':False,'prerelease':False,'assets':[
            {'name':name,'state':'uploaded','browser_download_url':f'https://github.com/{updates.REPOSITORY}/releases/download/v9.0.0/{name}'}]}

    def test_versions_and_download_origin(self):
        doc=self.release();u=updates.available_update(doc)
        self.assertEqual(u.version,'9.0.0');self.assertIn('/download/',u.download_url)
        doc['assets'][0]['browser_download_url']='https://other.invalid/download'
        self.assertEqual(updates.available_update(doc).download_url,u.release_url)
        for tag in ('v0.0.1','v'+updates.__version__):
            self.assertIsNone(updates.available_update({**doc,'tag_name':tag}))
        self.assertIsNone(updates.available_update({**doc,'prerelease':True}))
        for tag in ('v9.0.0/../../evil','9.0.0-rc1','9.x'):
            with self.assertRaises(ValueError):updates.available_update({**doc,'tag_name':tag})

    def test_daily_cache_manual_override_and_no_credentials(self):
        with tempfile.TemporaryDirectory() as d, patch.object(updates,'data_dir',return_value=Path(d)), patch.object(updates,'build_api_opener') as factory:
            opener=factory.return_value
            opener.open.side_effect=lambda *a,**k:io.BytesIO(json.dumps(self.release()).encode())
            first=updates.check_update();self.assertEqual(first,updates.check_update())
            self.assertEqual(opener.open.call_count,1)
            updates.check_update(force=True);self.assertEqual(opener.open.call_count,2)
            request=opener.open.call_args.args[0]
            self.assertFalse(request.has_header('Authorization'))
            self.assertEqual(request.full_url,updates.RELEASE_API)

    def test_download_architecture(self):
        for system,machine,name in [('darwin','arm64','macOS-arm64.zip'),('darwin','x86_64','macOS-x64.zip'),('linux','aarch64','Linux-arm64.tar.gz')]:
            with patch.object(updates.sys,'platform',system),patch.object(updates.platform,'machine',return_value=machine):
                self.assertTrue(updates.asset_name().endswith(name))
