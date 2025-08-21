import asyncio
import numpy as np
from caproto.server import pvproperty, PVGroup, ioc_arg_parser, run

class RandomIOC(PVGroup):
    """IOC publishing a random value PV."""
    random_value = pvproperty(value=0.0, name='Station_Laser:TestDevice:RandomValue')

    @random_value.startup
    async def random_value(self, instance, async_lib):
        """Update the PV with a new random number every 0.1 s."""
        while True:
            await self.random_value.write(float(np.random.random()))
            await asyncio.sleep(0.1)


if __name__ == "__main__":
    ioc_options, run_options = ioc_arg_parser(default_prefix='')
    ioc = RandomIOC(**ioc_options)
    run(ioc.pvdb, **run_options)
