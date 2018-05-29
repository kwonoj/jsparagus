#!/usr/bin/env python3

"""gen.py - Third stab at a parser generator.

**Nature of a grammar.**
A grammar is a dictionary {str: [[symbol]]} mapping names of nonterminals to lists of productions.
A production is a nonempty list of symbols.
Each symbol specifies either a kind of terminal, a nonterminal (by name),
or a Reduction (a namedtuple that guides the construction of the parse tree).

**Context of the generated parser.**
The user passes to each method an object representing the input sequence.
This object must support two methods:

*   `src.peek()` returns the kind of the next token, or `None` at the end of input.

*   `src.take(kind)` throws an exception if `src.peek() != kind`;
    otherwise, it removes the next token from the input stream and returns it.
    The special case `src.take(None)` checks that the input stream is empty:
    if so, it returns None; if not, it throws.

**Simplifying assumptions about the grammar.**
We assume the grammar is left-factored, or will be once we eliminate left recursion.
We verify that the grammar, after eliminating left recursion, is LL(1).

We assume that no production in the input grammar matches the empty
string. (However, eliminating left-recursion typically creates productions that
match the empty string, so it's unclear what this buys us, except that the
dragon book claims the algorithm to eliminate left-recursion only works if we
have no such productions to begin with—a claim I don't understand.)

We assume that every nonterminal matches at least one string of finite length.
It's not a bug if it doesn't, but it would be nice to check.
"""

import collections


# A Reduction is a step in a production that produces an AST node from the most recently parsed symbols.
Reduction = collections.namedtuple("Reduction", "tag_name tag_index arg_count")


# A symbol in a production is one of these three things:

def is_nt(element):
    return isinstance(element, str) and element[:1].islower()

def is_terminal(element):
    return isinstance(element, str) and not is_nt(element)

def is_reduction(element):
    return isinstance(element, Reduction)


def check(grammar):
    """Enforce three basic rules about the grammar.

    1.  Every nonterminal that appears in any production is defined.

    2.  The grammar contains no cycles.
        A cycle is a set of productions such that any
        nonterminal `q` has `q ==>+ q` (produces itself via at least one step).

    3.  No rule matches the empty string.

    If the grammar breaks any of these rules, throw.
    """

    # Maps names of nonterminals to one of:
    #     (missing) - we haven't seen this nt at all
    #     False - we are currently examining this nt for cycles
    #     True - we checked and this nt definitely is not in any cycles
    status = {}

    def check_nt(nt):
        s = status.get(nt)
        if s == True:
            return
        elif s == False:
            raise ValueError("invalid grammar: nonterminal {!r} has a cycle".format(nt))
        else:
            assert s is None
            status[nt] = False
            prods = grammar[nt]
            for prod in prods:
                for symbol in prod:
                    if is_nt(symbol) and symbol not in grammar:
                        raise ValueError("invalid grammar: nonterminal {!r} is used "
                                         "but not defined"
                                         .format(symbol))

                # Filter out reductions.
                prod = [symbol for symbol in prod if not is_reduction(symbol)]

                if len(prod) == 0:
                    raise ValueError("invalid grammar: nonterminal {!r} can match the empty string".format(nt))
                elif len(prod) == 1 and is_nt(prod[0]):
                    # Because we enforce rule 3 (no production can match the
                    # empty string), rule 2 is much easier to check: only
                    # productions consisting of exactly one nonterminal can be
                    # in cycles.
                    check_nt(prod[0])
            status[nt] = True

    for nt in grammar:
        check_nt(nt)


def eliminate_left_recursion(grammar):
    """Dragon book Algorithm 4.1."""

    def gensym(grammar, nt):
        """ Come up with a symbol name that's not already being used in the given grammar. """
        assert is_nt(nt)
        while nt in grammar:
            nt += "_"
        return nt

    def quasisort_nts():
        """ Make a half-hearted effort to put all the nts in a nice order for processing.

        Since the algorithm bans left-calls from later nts to earlier ones,
        the list should ideally be arranged so that such calls are rare; that is,
        as much as possible, if A left-calls B, then A should appear before B.

        We do a sort of topological sort to try to ensure this; but cycles are possible,
        and when we find one, we leave a left call that will be eliminated by a future
        call to eliminate_left_calls().
        """
        out = []
        stack = []
        def visit(nt):
            if nt not in out:
                stack.append(nt)
                for r in grammar[nt]:
                    if r and is_nt(r[0]):
                        if r[0] in stack:
                            pass # oh well
                        else:
                            visit(r[0])
                out.append(nt)
                stack.pop()

        for nt in grammar:
            visit(nt)

        assert sorted(grammar) == sorted(out)
        out.reverse()
        return out

    def eliminate_left_calls(from_nt, to_nt):
        """Rewrite productions of `from_nt` so that none start with the symbol `to_nt`.
        This is done by inlining all productions of `to_nt` into `from_nt`. (The result
        could be a combinatorial explosion, but in practice it's not that bad.)

        from_nt ::= to_nt a0 | ... | b0 | ...
        ==> from_nt ::= c0 a0 | ... | b0 | ...     where to_nt ::= c0 | ...

        That's a cross product of `c` and `a` productions.
        """
        grammar[from_nt] = (
            [r for r in grammar[from_nt]
                   if r[:1] != [to_nt]] +
            [c + a[1:] for c in grammar[to_nt]
                           for a in grammar[from_nt]
                               if a[:1] == [to_nt]]
        )

    def eliminate_immediate_left_recursion(nt):
        """Rewrite the productions of `nt` so that none start with the symbol `nt`.

        nt ::= nt a0 | ... | b0 | ...
        ==> nt ::= b0 nt' | ...
            nt' ::= (empty) | a0 nt' | ...
        """
        rules = grammar[nt]
        if any(r[:1] == [nt] for r in rules):
            epilogue = gensym(grammar, nt)
            grammar[epilogue] = [[]] + [r[1:] + [epilogue]
                                        for r in rules
                                            if r[:1] == [nt]]
            grammar[nt] = [r + [epilogue]
                           for r in rules
                               if r[:1] != [nt]]

    ntnames = quasisort_nts()
    for i, iname in enumerate(ntnames):
        for j, jname in enumerate(ntnames[:i]):
            eliminate_left_calls(from_nt=iname, to_nt=jname)
        eliminate_immediate_left_recursion(iname)


EMPTY = "(empty)"
END = "($)"


def start(grammar, symbol):
    """Compute the start set for the given symbol.

    A symbol's start set is the set of tokens that a match for that symbol
    may start with, plus EMPTY if the symbol can match the empty string.
    """
    if is_terminal(symbol):
        # There is only one allowed match for a terminal.
        return {symbol}
    elif is_reduction(symbol):
        # Reductions always match the empty string.
        return {EMPTY}
    else:
        # Each nonterminal has a start set that depends on its productions.
        assert is_nt(symbol)
        return set.union(*(seq_start(grammar, prod)
                           for prod in grammar[symbol]))


def seq_start(grammar, seq):
    """Compute the start set for a sequence of symbols."""
    s = {EMPTY}
    for symbol in seq:
        if EMPTY not in s:  # preceding symbols never match the empty string
            break
        s.remove(EMPTY)
        s |= start(grammar, symbol)
    return s


def follow_sets(grammar, goal):
    """Compute all follow sets for nonterminals in a grammar.

    The follow set for a nonterminal `A`, as defined in the book, is "the set
    of terminals that can appear immediately to the right of `A` in some
    sentential form"; plus, "If `A` can be the rightmost symbol in some
    sentential form, then $ is in FOLLOW(A)."

    The `goal` argument is necessary to specify what a sentential form is,
    since sentential forms are partial derivations of a particular goal
    nonterminal.

    Returns a default-dictionary mapping nts to follow sets.
    """

    # Set of nonterminals already seen, including those we are in the middle of
    # analyzing. The algorithm starts at `goal` and walks all reachable
    # nonterminals, recursively.
    visited = set()

    # The results. By definition, nonterminals that are not reachable from the
    # goal nt have empty follow sets.
    follow = collections.defaultdict(set)

    # If `(x, y) in subsumes_relation`, then x can appear at the end of a
    # production of y, and therefore follow[x] should be <= follow[y].
    # (We could maintain that invariant throughout, but at present we
    # brute-force iterate to a fixed point at the end.)
    subsumes_relation = set()

    # `END` is $. It is, of course, in follow[goal]. It gets into other
    # nonterminals' follow sets through the subsumes relation.
    follow[goal].add(END)

    def visit(nt):
        if nt in visited:
            return
        visited.add(nt)
        for prod in grammar[nt]:
            for i, symbol in enumerate(prod):
                if is_nt(symbol):
                    visit(symbol)
                    after = seq_start(grammar, prod[i + 1:])
                    if EMPTY in after:
                        after.remove(EMPTY)
                        subsumes_relation.add((symbol, nt))
                    follow[symbol] |= after

    visit(goal)

    # Now iterate to a fixed point on the subsumes relation.
    done = False
    while not done:
        done = True # optimistically
        for target, source in subsumes_relation:
            if follow[source] - follow[target]:
                follow[target] |= follow[source]
                done = False

    return follow


def dump_grammar(grammar):
    for nt, rules in sorted(grammar.items()):
        print(nt + " ::=")
        for s in rules:
            print("   ", s)


def check_ambiguity(grammar, goal):
    """Throw if the given grammar, which must already be non-left-recursive, isn't LL(1)."""
    follow = follow_sets(grammar, goal)
    for nt, prods in grammar.items():
        start = set()
        for prod in prods:
            prod_start = seq_start(grammar, prod)
            conflicts = prod_start & start
            if conflicts:
                # The grammar is not LL(1). It may not actually be ambiguous,
                # but this simplistic analysis can't prove it unambiguous.
                if conflicts == {EMPTY}:
                    # Definitely ambiguous.
                    raise ValueError("ambiguous grammar: multiple productions for {!r} "
                                     "match the empty string".format(nt))
                else:
                    conflicts -= {EMPTY}
                    raise ValueError("unsupported grammar: multiple productions for {!r} "
                                     "match strings that start with {!r}"
                                     .format(nt, list(conflicts)[0]))
            start |= prod_start

        # If nt can match the empty string, then we also have to check that
        # there is no ambiguity between matching the empty string and matching
        # a nonempty string. This is done by comparing the start set we've just
        # computed with nt's follow set. (If the grammar is left-recursive, this
        # step will error out, even though the grammar is not really ambiguous.)
        if EMPTY in start:
            conflicts = start & follow[nt]
            if conflicts:
                raise ValueError("unsupported grammar: the token {!r} could start either "
                                 "a string matching {!r} or something that follows it"
                                 .format(list(conflicts)[0], nt))


def generate_parser(out, grammar, goal):
    # First, append a natural reduction step at the end of every production.
    # This ensures that the parser we eventually generate builds parse trees
    # matching the *original* grammar, no matter how we transform the grammar
    # internally.
    grammar = {nt: [prod + [Reduction(nt, i, len(prod))]
                    for i, prod in enumerate(productions)]
               for nt, productions in grammar.items()}

    check(grammar)
    eliminate_left_recursion(grammar)
    # XXX TODO left-factoring
    check_ambiguity(grammar, goal)

    write = out.write

    for nt, rules in grammar.items():
        write("def parse_{}(src, stack):\n".format(nt))
        write("    token = src.peek()\n")

        if_keyword = "if"

        # Set of terminals that can be the first token of a match for any rule
        # we've considered so far. We track this to rule out ambiguity (overzealously).
        #
        # We track this set even when we're emitting code for left-recursive productions;
        # it's not necessary, because check() imposes a much tougher rule, but the extra
        # checking doesn't hurt anything.
        seen = set()
        empty_production = None
        for i, rule in enumerate(rules):
            start_set = seq_start(grammar, rule)
            if start_set & seen:
                raise ValueError("invalid grammar: ambiguous token(s) {}".format(start_set & seen))
            seen |= start_set
            if seen == {EMPTY}:
                assert empty_production is None
                empty_production = i
            else:
                if len(start_set) == 1:
                    match_expr = "token == {!r}".format(list(start_set)[0])
                else:
                    match_expr = "token in {!r}".format(tuple(start_set))
                write("    {} {}:\n".format(if_keyword, match_expr))
                if_keyword = "elif"
                for element in rule:
                    if is_terminal(element):
                        write("        stack.append(src.take({!r}))\n".format(element))
                    elif is_nt(element):
                        write("        parse_{}(src, stack)\n".format(element))
                    else:
                        write("        args = stack[-{}:]\n".format(element.arg_count))
                        write("        del stack[-{}:]\n".format(element.arg_count))
                        write("        stack.append(({!r}, {!r}, args))\n".format(element.tag_name, element.tag_index))
        if empty_production is None:
            write("    else:\n")
            write("        raise ValueError({!r}.format(token))\n".format("expected " + nt + ", got {!r}"))

        write("\n")

    # Write entry point.
    write("def parse(src):\n")
    write("    stack = []\n")
    write("    parse_{}(src, stack)\n".format(goal))
    write("    src.take(None)\n")
    write("    assert len(stack) == 1\n")
    write("    return stack[0]\n")

def main():
    grammar = {
        'expr': [
            ['term'],
            ['expr', '+', 'term'],
            ['expr', '-', 'term'],
        ],
        'term': [
            ['prim'],
            ['term', '*', 'prim'],
            ['term', '/', 'prim'],
        ],
        'prim': [
            ['NUM'],
            ['VAR'],
            ['(', 'expr', ')'],
        ],
    }

    class Tokens:
        def __init__(self, space_separated):
            self.tokens = space_separated.split()

        def peek(self):
            if len(self.tokens) == 0:
                return None
            else:
                next = self.tokens[0]
                if next.isdigit():
                    return "NUM"
                elif next.isalpha():
                    return "VAR"
                elif next in '+-*/()':
                    return next
                else:
                    raise ValueError("unexpected token {!r}".format(next))

        def take(self, k):
            if k is None:
                if self.tokens:
                    raise ValueError("expected end of input")
            else:
                assert self.peek() == k
                return self.tokens.pop(0)

    import io
    out = io.StringIO()
    generate_parser(out, grammar, 'expr')
    code = out.getvalue()
    print(code)
    print("----")

    sandbox = {}
    exec(code, sandbox)
    parse = sandbox['parse']

    while True:
        try:
            line = input('> ')
        except EOFError as _:
            break
        tokens = Tokens(line)
        try:
            result = parse(tokens)
        except Exception as exc:
            print(exc)
        else:
            print(result)

if __name__ == '__main__':
    main()
