import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hub.wifi import _split, _explain  # noqa: E402


class WifiParseTest(unittest.TestCase):
    def test_split_plain(self):
        self.assertEqual(_split('*:TP-Link-1700:79:WPA2:40'), ['*', 'TP-Link-1700', '79', 'WPA2', '40'])

    def test_split_escaped_colon_and_backslash(self):
        self.assertEqual(_split(r' :my\:net:50:WPA1 WPA2:6'), [' ', 'my:net', '50', 'WPA1 WPA2', '6'])
        self.assertEqual(_split(r'a\\b:c'), ['a\\b', 'c'])

    def test_split_empty_fields(self):
        self.assertEqual(_split(' ::69:WPA2:4'), [' ', '', '69', 'WPA2', '4'])

    def test_explain(self):
        self.assertIn('password', _explain('Error: Connection activation failed: Secrets were required, but not provided'))
        self.assertIn('not found', _explain("Error: No network with SSID 'x' found."))
        self.assertEqual(_explain('Error: Connection activation failed: The Wi-Fi network could not be found\n'
                                  "Hint: use 'journalctl -xe NM_CONNECTION=abc' to get more details."),
                         'network not found nearby')


if __name__ == '__main__':
    unittest.main()
