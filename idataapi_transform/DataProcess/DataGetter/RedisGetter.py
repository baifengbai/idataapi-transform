import asyncio
import random
import logging
import traceback
import json
from .BaseGetter import BaseGetter
from ..Config.MainConfig import main_config


class RedisGetter(BaseGetter):
    def __init__(self, config):
        super().__init__(self)
        if not main_config.has_es_configured:
            raise ValueError("You must config redis before using ESGetter, Please edit configure file: %s" % (main_config.ini_path, ))
        self.config = config
        self.is_range = self.config.is_range
        self.need_del = self.config.need_del
        self.responses = list()
        self.need_clear = False
        self.done = False
        self.total_size = None
        self.miss_count = 0
        self.total_count = 0
        self.redis_object_length = 0

    def init_val(self):
        self.responses = list()
        self.need_clear = False
        self.done = False
        self.miss_count = 0
        self.total_count = 0
        self.redis_object_length = 0

    def __aiter__(self):
        return self

    async def __anext__(self, retry=1):
        await self.config.get_redis_pool_cli()  # init redis pool
        if self.is_range and self.total_size is None:
            self.redis_object_length = await self.config.redis_len_method(self.config.key)
            self.total_size = self.config.max_limit if (self.config.max_limit and self.config.max_limit < self.redis_object_length) else self.redis_object_length

        if self.need_clear:
            self.responses.clear()
            self.need_clear = False

        if self.done:
            logging.info("get source done: %s, total get %d items, total filtered: %d items" %
                         (self.config.name, self.total_count, self.miss_count))
            self.init_val()
            raise StopAsyncIteration

        if self.is_range:
            if self.config.direction == "L":
                left = self.total_count
                right = self.total_count + self.config.per_limit - 1
            else:
                left = self.total_size - self.config.per_limit - 1
                if left < 0:
                    left = 0
                right = left + self.config.per_limit

            try:
                self.responses = await self.config.redis_read_method(self.config.key, left, right)
                self.responses = [json.loads(i) for i in self.responses]
            except Exception as e:
                if retry < self.config.max_retry:
                    await asyncio.sleep(random.randint(self.config.random_min_sleep, self.config.random_max_sleep))
                    return await self.__anext__(retry+1)
                else:
                    logging.error("Give up redis getter, After retry: %d times, still fail to get key: %s, "
                                  "total get %d items, total filtered: %d items, error: %s" % (self.config.max_retry, self.config.key, self.total_count, self.miss_count, str(traceback.format_exc())))
                    raise StopAsyncIteration

            self.need_clear = True
            if len(self.responses) < self.config.per_limit or not self.responses:
                self.done = True
            if self.need_del:
                await self.config.redis_del_method(self.config.key, self.total_count + self.config.per_limit, -1)
        else:

            try:
                self.responses = await self.config.redis_read_method(self.config.key)
                self.responses = [json.loads(i) for i in self.responses.values()]
            except Exception as e:
                if retry < self.config.max_retry:
                    await asyncio.sleep(random.randint(self.config.random_min_sleep, self.config.random_max_sleep))
                    return await self.__anext__(retry + 1)
                else:
                    logging.error("Give up redis getter, After retry: %d times, still fail to get key: %s, "
                                  "total get %d items, total filtered: %d items, reason: %s" %
                                  (self.config.max_retry, self.config.key, self.total_count, self.miss_count, str(traceback.format_exc())))
                    raise StopAsyncIteration

            if self.config.max_limit:
                self.responses = self.responses[:self.config.max_limit]
            self.need_clear = self.done = True
            if self.need_del:
                await self.config.redis_del_method(self.config.key)

        current_response_length = len(self.responses)
        self.total_count += current_response_length
        if self.config.filter:
            target_responses = list()
            for i in self.responses:
                if self.config.filter:
                    i = self.config.filter(i)
                if i:
                    target_responses.append(i)
                else:
                    self.miss_count += 1
            self.responses = target_responses

        if self.is_range:
            logging.info("Get %d items from %s, filtered: %d items, percentage: %.2f%%" %
                         (current_response_length, self.config.name, self.miss_count,
                          (self.total_count / self.total_size * 100) if self.total_size else 0))
        return self.responses

    def __iter__(self):
        raise ValueError("RedisGetter must be used with async generator, not normal generator")