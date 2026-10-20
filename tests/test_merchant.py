import unittest

from src.analytics.merchant import normalize


class NormalizeTestCase(unittest.TestCase):
    def test_strips_reward_point_suffix(self):
        self.assertEqual(normalize('ACME MARKET ISTANBUL TR KAZANILAN MAXIPUAN:1,20'), 'ACME MARKET')
        self.assertEqual(normalize('ACME MARKET ISTANBUL TR KAZANILAN MAXİPUAN:9,20 3 6 1 1'),
                         'ACME MARKET')
        self.assertEqual(normalize('ACME MARKET ISTANBUL TR KAZANILAN MAXIMIL:4,00'), 'ACME MARKET')

    def test_strips_cancellation_marker(self):
        self.assertEqual(normalize('ACME SPOR ISTANBUL TR İŞLEM İPTALI'), 'ACME SPOR')
        self.assertEqual(normalize('ACME ONLINE LONDON GB İPTAL'), 'ACME ONLINE')

    def test_strips_processor_and_installment_prefixes(self):
        self.assertEqual(normalize('TAKSİTLENDİRME/ACME SPOR ISTANBUL TR'), 'ACME SPOR')
        self.assertEqual(normalize('PARATIKA/ACME.COM.TR ISTANBUL TU'), 'ACME.COM.TR')
        self.assertEqual(normalize('ARTI TAKSIT TAKSITLENDIRILMESI /ACME SPOR ISTANBUL TR'),
                         'ACME SPOR')
        self.assertEqual(normalize('İADE/ACME OYUNCAK'), 'ACME OYUNCAK')

    def test_strips_reversed_spaced_footer_text(self):
        self.assertEqual(
            normalize('ACME SPOR ISTANBUL TR 4 0 7 1 9 - 3 4 : ı s a r a m u N e g l e B'),
            'ACME SPOR')

    def test_strips_store_numbers_and_location(self):
        self.assertEqual(normalize('ACME 1234 ANKARA TR'), 'ACME')
        self.assertEqual(normalize('ACME CLOUD SAN FRANCISCOCAUS 5406**422'),
                         'ACME CLOUD SAN FRANCISCOCAUS')

    def test_keeps_digits_inside_a_name(self):
        self.assertEqual(normalize('ACME101 MARKET'), 'ACME101 MARKET')
        self.assertEqual(normalize('ACME&02 ISTANBUL TR'), 'ACME&02')

    def test_turkish_aware_upper_case(self):
        self.assertEqual(normalize('acme kırtasiye'), 'ACME KIRTASİYE')

    def test_variants_of_one_merchant_collapse(self):
        variants = [
            'ACME SPOR ISTANBUL TR',
            'TAKSİTLENDİRME/ACME SPOR ISTANBUL TR',
            'ACME SPOR ISTANBUL TR İŞLEM İPTALI',
            'ACME SPOR ISTANBUL TR KAZANILAN MAXIPUAN:0,39',
        ]
        self.assertEqual({normalize(v) for v in variants}, {'ACME SPOR'})

    def test_empty(self):
        self.assertEqual(normalize(''), '')
        self.assertEqual(normalize(None), '')


if __name__ == '__main__':
    unittest.main()
