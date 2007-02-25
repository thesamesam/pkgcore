# Copyright: 2005-2007 Brian Harring <ferringb@gentoo.org>
# License: GPL2

from pkgcore.test import TestCase

from pkgcore.ebuild import conditionals
from pkgcore.ebuild.errors import ParseError
from pkgcore.restrictions import boolean, packages
from pkgcore.util.currying import post_curry
from pkgcore.util.iterables import expandable_chain
from pkgcore.util.lists import iflatten_instance


class base(TestCase):

    class kls(conditionals.DepSet):
        parse_depset = None

    def gen_depset(self, string, operators=None, func=None):
        if func is not None:
            kwds = {"element_func":func}
        else:
            kwds = {}
        if operators is None:
            operators = {"":boolean.AndRestriction, "||":boolean.OrRestriction}
        return self.kls(string, str, operators=operators, **kwds)


class native_DepSetParsingTest(base):

    def f(self, x):
        self.assertRaises(ParseError, self.gen_depset, x)

    # generate a lot of parse error assertions.
    for x in ("( )", "( a b c", "(a b c )",
        "( a b c)", "()", "x?( a )",
        "?x (a)", "x? (a )", "x? (a)", "x? ( a b)",
        "x? ( x? () )", "x? ( x? (a)", "(", ")", "x?",
        "||(", "||()", "||( )", "|| ()",
        "|| (", "|| )", "||)",	"|| ( x? ( )",
        "|| ( x?() )", "|| (x )", "|| ( x)",
        "a|", "a?", "a(b", "a)", "a||b",
        "a(", "a)b", "x? y", "( x )?", "||?"):
        locals()["test assert ParseError '%s'" % x] = post_curry(f, x)
    del x

    @staticmethod
    def mangle_cond_payload(p):
        l = [p]
        if isinstance(p, boolean.AndRestriction):
            l = iter(p)
        for x in l:
            s = ""
            if x.negate:
                s = "!"
            for y in x.vals:
                yield s + y


    def flatten_restricts(self, v):
        i = expandable_chain(v)
        depth = 0
        conditionals = []
        for x in i:
            for t, s in ((boolean.OrRestriction, "||"),
                         (boolean.AndRestriction, "&&")):
                if isinstance(x, t):
                    yield s
                    yield "("
                    i.appendleft(")")
                    i.appendleft(x.restrictions)
                    depth += 1
                    break
            else:
                if isinstance(x, packages.Conditional):
                    self.assertTrue(x.attr == "use")
                    conditionals.insert(
                        depth, list(self.mangle_cond_payload(x.restriction)))
                    yield set(iflatten_instance(conditionals[:depth + 1]))
                    yield "("
                    i.appendleft(")")
                    i.appendleft(x.payload)
                    depth += 1
                else:
                    if x == ")":
                        self.assertTrue(depth)
                        depth -= 1
                    yield x
        self.assertFalse(depth)

    def check_depset(self, s, func=base.gen_depset):
        if isinstance(s, (list, tuple)):
            s, v = s
            v2 = []
            for idx, x in enumerate(v):
                if isinstance(x, (list, tuple)):
                    v2.append(set(x))
            v = v2
        else:
            v = s.split()
        self.assertEqual(list(self.flatten_restricts(func(self, s))), list(v))

    def check_str(self, s, func=base.gen_depset):
        if isinstance(s, (list, tuple)):
            s, v = s
            v2 = []
            for x in v:
                if isinstance(x, basestring):
                    v2.append(x)
                else:
                    v2.append(x[-1] + '?')
            v = ' '.join(v2)
        else:
            v = ' '.join(s.split())
        v = ' '.join(v.replace("&&", "").split())
        self.assertEqual(str(func(self, s)), v)

    # generate a lot of assertions of parse results.
    # if it's a list, first arg is string, second is results, if
    # string, the results for testing are determined by splitting the string
    for x in [
        "a b",
        ( "", 	[]),

        ( "( a b )",	("&&", "(", "a", "b", ")")),

        "|| ( a b )",

        ( "a || ( a ( b  ) c || ( d )  )",
            ["a", "||", "(", "a", "b", "c", "d", ")"]),

        ( " x? ( a  b )",
            (["x"], "(", "a", "b", ")")),

        # at some point, this should collapse it
        ( "x? ( y? ( a ) )",
            (["x"], "(", ["x", "y"], "(", "a", ")", ")")),

        # at some point, this should collapse it
        ("|| ( || ( a b ) )", ["||", "(", "a", "b", ")"]),

        # at some point, this should collapse it
        "|| ( || ( a b ) c )",

        ( "x? ( a !y? ( || ( b c ) d ) e ) f1 f? ( g h ) i",
            (
            ["x"], "(", "a", ["x", "!y"], "(", "||", "(", "b",
            "c", ")", "d", ")", "e", ")", "f1",
            ["f"], "(", "g", "h", ")", "i"
            )
        )]:

        if isinstance(x, (list, tuple)):
            name = "'%s'" % x[0]
        else:
            name = "'%s'" % x
#        locals()["test_parse %s" % name] = post_curry(check_depset, x)
        locals()["test_str %s" % name] = post_curry(check_str, x)

    def check_known_conditionals(self, text, conditionals):
        d = self.gen_depset(text)
        self.assertEqual(sorted(d.known_conditionals),
            sorted(conditionals.split()))
        # ensure it does the lookup *once*
        object.__setattr__(d, 'restrictions', ())
        self.assertFalse(d.restrictions)
        self.assertEqual(sorted(d.known_conditionals),
            sorted(conditionals.split()))

    for x, c in [
        ["a? ( b )", "a"],
        ["a? ( b a? ( c ) )", "a"],
        ["a b c d e ( f )", ""],
        ["!a? ( b? ( c ) )", "a b"]
        ]:
        locals()["test_known_conditionals %s" % x] = post_curry(
            check_known_conditionals, x, c)
    del x,c
        

    def test_element_func(self):
        self.assertEqual(
            self.gen_depset("asdf fdas", func=post_curry(str)).element_class,
            "".__class__)

    def test_disabling_or(self):
        self.assertRaises(
            ParseError, self.gen_depset, "|| ( a b )",
            {"operators":{"":boolean.AndRestriction}})


class cpy_DepSetParsingTest(native_DepSetParsingTest):

    kls = staticmethod(conditionals.DepSet)
    if not conditionals.DepSet.parse_depset:
        skip = "extension not available"


class native_DepSetConditionalsInspectionTest(base):

    def test_sanity_has_conditionals(self):
        self.assertFalse(bool(self.gen_depset("a b").has_conditionals))
        self.assertFalse(bool(
                self.gen_depset("( a b ) || ( c d )").has_conditionals))
        self.assertTrue(bool(self.gen_depset("x? ( a )").has_conditionals))
        self.assertTrue(bool(self.gen_depset("( x? ( a ) )").has_conditionals))

    def flatten_cond(self, c):
        l = set()
        for x in c:
            if isinstance(x, boolean.base):
                self.assertEqual(len(x.dnf_solutions()), 1)
                f = x.dnf_solutions()[0]
            else:
                f = [x]
            t = set()
            for a in f:
                s = ""
                if a.negate:
                    s = "!"
                t.update(["%s%s" % (s, y) for y in a.vals])
            l.add(frozenset(t))
        return l

    def check_conds(self, s, r, msg=None):
        nc = dict(
            (k, self.flatten_cond(v))
            for (k, v) in self.gen_depset(s).node_conds.iteritems())
        d = dict(r)
        for k, v in d.iteritems():
            if isinstance(v, basestring):
                d[k] = set([frozenset(v.split())])
            elif isinstance(v, (tuple, list)):
                d[k] = set(map(frozenset, v))
        self.assertEqual(nc, d, msg)

    for s in (
        ("x? ( y )", {"y":"x"}),
        ("x? ( y ) z? ( y )", {"y":["z", "x"]}),
        ("x? ( z? ( w? ( y ) ) )", {"y":"w z x"}),
        ("!x? ( y )", {"y":"!x"}),
        ("!x? ( z? ( y a ) )", {"y":"!x z", "a":"!x z"}),
        ("x ( y )", {}),
        ("x ( y? ( z ) )", {"z":"y"}, "needs to dig down as deep as required"),
        ("x y? ( x )", {}, "x isn't controlled by a conditional, shouldn't be "
         "in the list"),
        ("|| ( y? ( x ) x )", {}, "x cannot be filtered down since x is "
         "accessible via non conditional path"),
        ("|| ( y? ( x ) z )", {"x":"y"}),
        ):
        locals()["test _node_conds %s" % s[0]] = post_curry(check_conds, *s)


class cpy_DepSetConditionalsInspectionTest(
    native_DepSetConditionalsInspectionTest):

    kls = staticmethod(conditionals.DepSet)
    if not conditionals.DepSet.parse_depset:
        skip = "extension not available"


def convert_to_seq(s):
    if isinstance(s, (list, tuple)):
        return s
    return [s]


class native_DepSetEvaluateTest(base):

    def test_evaluation(self):
        for vals in (("y", "x? ( y ) !x? ( z )", "x"),
            ("z", "x? ( y ) !x? ( z )"),
            ("", "x? ( y ) y? ( z )"),
            ("a b", "a !x? ( b )"),
            ("a b", "a !x? ( b )", "", ""),
            ("a b", "a !x? ( b ) y? ( c )", "", "y"),
            ("a || ( c )", "a || ( x? ( b ) c )"),
            ("a b", "a b"),
            ):
            result = vals[0]
            s = vals[1]
            use, tristate = [], None
            if len(vals) > 2:
                use = convert_to_seq(vals[2])
            if len(vals) > 3:
                tristate = convert_to_seq(vals[3])
            orig = self.gen_depset(s)
            collapsed = orig.evaluate_depset(use,
                tristate_filter=tristate)
            self.assertEqual(str(collapsed), result, msg=
                "expected %r got %r\nraw depset: %r\nuse: %r, tristate: %r" %
                    (result, str(collapsed), s, use, tristate))
            if '?' not in s:
                self.assertIdentical(orig, collapsed)


class cpy_DepSetEvaluateTest(native_DepSetEvaluateTest):

    kls = staticmethod(conditionals.DepSet)
    if not conditionals.DepSet.parse_depset:
        skip = "extension not available"
