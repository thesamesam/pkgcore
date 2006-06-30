# Copyright: 2005 Marien Zwart <marienz@gentoo.org>
# License: GPL2


from twisted.trial import unittest
from pkgcore.util import lists


class UnhashableComplex(complex):

	def __hash__(self):
		raise TypeError


class FlattenTest(unittest.TestCase):

	def test_flatten(self):
		self.assertEquals(lists.flatten([(1), (2, [3, 4])]), [1, 2, 3, 4])
		self.assertEquals(lists.flatten(()), [])
		self.assertEquals(
			lists.flatten(['foo', ('bar', 'baz')]),
			['foo', 'bar', 'baz'])



class UniqueTest(unittest.TestCase):

	def common_check(self, f):
		# silly
		self.assertEquals(f(()), [])
		# hashable
		self.assertEquals(sorted(f([1, 1, 2, 3, 2])), [1, 2, 3])
		# neither

	def test_stable_unique(self):
		self.common_check(lists.stable_unique)

	def test_unstable_unique(self):
		self.common_check(lists.unstable_unique)
		uc = UnhashableComplex
		res = lists.unstable_unique([uc(1, 0), uc(0, 1), uc(1, 0)])
		# sortable
		self.assertEquals(sorted(lists.unstable_unique(
					[[1, 2], [1, 3], [1, 2], [1, 3]])), [[1, 2], [1, 3]])
		self.failUnless(
			res == [uc(1, 0), uc(0, 1)] or res == [uc(0, 1), uc(1, 0)], res)


class ChainedListsTest(unittest.TestCase):

	@staticmethod
	def gen_cl():
		return lists.ChainedLists(range(3), range(3,6), range(6, 100))

	def test_contains(self):
		cl = self.gen_cl()
		for x in (1, 2, 4, 99):
			self.assertTrue(x in cl)

	def test_iter(self):
		self.assertEquals(list(self.gen_cl()), list(xrange(100)))
	
	def test_len(self):
		self.assertEquals(100, len(self.gen_cl()))

	def test_getitem(self):
		cl = self.gen_cl()
		for x in (1, 2, 4, 98, -1, -99, 0):
			cl[x]
		self.assertRaises(IndexError, cl.__getitem__, 100)
		self.assertRaises(IndexError, cl.__getitem__, -101)

	def test_mutable(self):
		self.assertRaises(TypeError, self.gen_cl().__delitem__, 1)
		self.assertRaises(TypeError, self.gen_cl().__setitem__, (1, 2))
