import unittest

from sample.pagination import paginate


class PaginationTests(unittest.TestCase):
    def test_first_page(self):
        self.assertEqual(paginate([1, 2, 3, 4, 5], 1, 2), [1, 2])

    def test_last_partial_page(self):
        self.assertEqual(paginate([1, 2, 3, 4, 5], 3, 2), [5])

    def test_empty_and_beyond_end(self):
        self.assertEqual(paginate([], 1, 2), [])
        self.assertEqual(paginate([1], 5, 2), [])

    def test_invalid_arguments(self):
        for page, size in ((0, 2), (1, 0), (-1, 2)):
            with self.assertRaises(ValueError):
                paginate([1], page, size)
