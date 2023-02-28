import click


class CommandWithOptionalFlagValues(click.Command):
    """A Command subclass that translate any flag `--opt=value` as flag `--opt` with changed flag_value=value"""

    def parse_args(self, ctx, args):
        flags = [
            o for o in self.params if isinstance(o, click.Option) and o.is_flag and not isinstance(o.flag_value, bool)
        ]
        prefixes = {p: o for o in flags for p in o.opts if p.startswith("--")}
        for i, flag in enumerate(args):
            flag = flag.split("=")
            if flag[0] in prefixes and len(flag) > 1:
                prefixes[flag[0]].flag_value = flag[1]
                args[i] = flag[0]

        return super(CommandWithOptionalFlagValues, self).parse_args(ctx, args)


def batch(iterable, size=1):
    l = len(iterable)
    for ndx in range(0, l, size):
        yield iterable[ndx : min(ndx + size, l)]
