import asyncio
import logging

from actorlib import actor, collect_actors, ActorNode, ActorContext, NodeSpecSchema


LOG = logging.getLogger(__name__)


@actor('registery.register')
def do_register(ctx: ActorContext, node: NodeSpecSchema):
    LOG.info(f'register node {node}')
    ctx.registery.add(node)
    ctx.send('registery.check', dict(node=node))


@actor('registery.check')
async def do_check(ctx: ActorContext, node: NodeSpecSchema):
    LOG.info('ping node {}'.format(node['name']))
    await ctx.send('worker.ping', {'message': 'ping'}, dst_node=node['name'])
    next_task = ctx.send('registery.check', dict(node=node))
    asyncio.get_event_loop().call_later(10, asyncio.ensure_future, next_task)


@actor('registery.query')
async def do_query(ctx: ActorContext):
    return dict(
        current_node=ctx.registery.current_node.to_spec(),
        registery=ctx.registery.to_spec(),
    )


ACTORS = collect_actors(__name__)


def main():
    app = ActorNode(
        actors=ACTORS,
        port=8081,
        name='registery',
        subpath='/api/v1/registery',
    )
    app.run()


if __name__ == "__main__":
    from rssant_common.logger import configure_logging
    configure_logging()
    main()