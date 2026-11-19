from ..services import bias


class BiasController:
    catalog = staticmethod(bias.list_timers)
    save_timer = staticmethod(bias.save_timer)
    save_pair_rule = staticmethod(bias.save_pair_rule)
    batches = staticmethod(bias.list_batches)
    specimens = staticmethod(bias.list_specimens)
    add_specimens = staticmethod(bias.add_specimens)
    list_analyses = staticmethod(bias.list_analyses)
    create_analysis = staticmethod(bias.create_analysis)
    get_analysis = staticmethod(bias.get_analysis)
    recompute = staticmethod(bias.recompute)
    publish = staticmethod(bias.publish)
    recover = staticmethod(bias.recover)
