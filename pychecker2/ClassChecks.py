
from pychecker2.Check import Check
from pychecker2.Check import Warning
from pychecker2 import symbols
from pychecker2.util import BaseVisitor, parents, type_filter

from compiler.misc import mangle
from compiler import ast, walk

_ignorable = {}
for i in ['repr', 'dict', 'class', 'doc', 'str']:
    _ignorable['__%s__' % i] = 1

class GetDefs(BaseVisitor):
    "Record definitions of a attribute of self, who's name is provided"
    def __init__(self, name):
        self.selfname = name
        self.result = {}

    def visitAssAttr(self, node):
        if isinstance(node.expr, ast.Name) and \
           node.expr.name == self.selfname and \
           isinstance(node.parent, (ast.Assign, ast.AssTuple)):
            self.result[node.attrname] = node

class GetRefs(BaseVisitor):
    "Record references to a attribute of self, who's name is provided"
    def __init__(self, name):
        self.selfname = name
        self.result = {}

    def visitAssAttr(self, node):
        if isinstance(node.expr, ast.Name) and \
           node.expr.name == self.selfname:
            self.result[node.attrname] = node
        self.visitChildren(node)

    def visitGetattr(self, node):
        if isinstance(node.expr, ast.Name) and \
           node.expr.name == self.selfname:
            self.result[node.attrname] = node
        self.visitChildren(node)


def get_methods(class_scope):
    return type_filter(class_scope.get_children(), symbols.FunctionScope)

def line(node):
    while node:
        if node.lineno is not None:
            return node
        node = node.parent
    return None

class NotSimpleName(Exception): pass

def get_name(node):
    while node:
        if isinstance(node, ast.Getattr):
            return get_name(node.expr) + "." + node.attrname
        elif isinstance(node, ast.Name):
            return node.name
    else:
        raise NotSimpleName(node)

def get_base_names(scope):
    names = []
    for b in scope.node.bases:
        if b:
            try:
                names.append(get_name(b))
            except NotSimpleName:       # FIXME: hiding expressions
                pass
    return names

def find_defs(scope, names):
    "Drill down scopes to find definition of x.y.z"
    root = names[0]
    for c in type_filter(scope.get_children(),
                         symbols.FunctionScope, symbols.ClassScope):
        if getattr(c, 'name', '') == root:
            if len(names) == 1:
                return c
            return find_defs(c, names[1:])
    # maybe defined by import
    if scope.imports.has_key(root):
        return None                     # FIXME
    return None

def find_local_class(scope, name):
    "Search up to find scope defining x of x.y.z"
    parts = name.split('.')
    for p in parents(scope):
        if p.defs.has_key(parts[0]):
            return find_defs(p, parts)
    return None

def get_bases(scope):
    result = []
    # FIXME: only finds local classes
    for name in get_base_names(scope):
        base = find_local_class(scope, name)
        if base:
            result.append(base)
            result.extend(get_bases(base))
    return result

class AttributeCheck(Check):
    "check `self.attr' expressions for attr"

    hasAttribute = Warning('Report unknown object attributes in methods',
                           'Class %s has no attribute %s')
    missingSelf = Warning('Report methods without "self"',
                          'Method %s is missing self parameter')
    methodRedefined = Warning('Report the redefinition of class methods',
                              'Method %s defined at line %d in '
                              'class %s redefined')

    def check(self, file):
        def visit_with_self(Visitor, method):
            # find self
            if not method.node.argnames:
                file.warning(method.node, self.missingSelf, method.node.name)
                return {}
            return walk(method.node, Visitor(method.node.argnames[0])).result

        # for all class scopes
        for scope in type_filter(file.scopes.values(), symbols.ClassScope):
            bases = get_bases(scope)
            # get attributes defined on self
            attributes = {}             # "self.foo = " kinda things
            methods = {}                # methods -> scopes
            inherited = {}              # all class defs
            for base in [scope] + bases:
                for m in get_methods(base):
                    attributes.update(visit_with_self(GetDefs, m))
                    methods[m.name] = methods.get(m.name, m)
                inherited.update(base.defs)

            # complain about defs with the same name as methods
            for name, node in attributes.items():
                try:
                    orig = methods[mangle(name, scope.name)]
                    file.warning(line(node), self.methodRedefined,
                                 name, orig.lineno, scope.name)
                    break
                except KeyError:
                    pass

            # find refs on self
            refs = []
            for m in get_methods(scope):
                refs.extend(visit_with_self(GetRefs, m).items())

            # Now complain about refs on self that aren't known
            for name, node in refs:
                if not attributes.has_key(name) and \
                   not _ignorable.get(name, None) and \
                   not scope.defs.has_key(mangle(name, scope.name)) and \
                   not inherited.has_key(name):
                    file.warning(line(node), self.hasAttribute,
                                 scope.name, name)