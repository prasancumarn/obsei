import logging

from typing import Any, Dict, List, Optional

from searchtweets import collect_results, gen_request_parameters, load_credentials

from obsei.source.base_source import BaseSource, BaseSourceConfig, SourceResponse

import preprocessor as cleaning_processor

logger = logging.getLogger(__name__)

DEFAULT_MAX_TWEETS = 10

DEFAULT_TWEET_FIELDS = [
    "author_id", "conversation_id", "created_at", "entities", "geo", "id", "in_reply_to_user_id", "lang",
    "public_metrics", "referenced_tweets", "source", "text"
]
DEFAULT_EXPANSIONS = [
    "author_id", "entities.mentions.username", "geo.place_id", "in_reply_to_user_id", "referenced_tweets.id",
    "referenced_tweets.id.author_id"
]
DEFAULT_PLACE_FIELDS = ["contained_within", "country", "country_code", "full_name", "geo", "id", "name", "place_type"]
DEFAULT_USER_FIELDS = [
    "created_at", "description", "entities", "id", "location", "name", "public_metrics", "url", "username", "verified"
]
DEFAULT_OPERATORS = [
    "-is:reply",
    "-is:retweet"
]


class TwitterSourceConfig(BaseSourceConfig):
    def __init__(
        self,
        twitter_config_filename: str = None,
        query: str = None,
        keywords: List[str] = None,
        hashtags: List[str] = None,
        usernames: List[str] = None,
        operators: Optional[List[str]] = None,
        since_id: Optional[int] = None,
        until_id: Optional[int] = None,
        # 10d
        # 15m
        lookup_period: str = None,
        tweet_fields: Optional[List[str]] = None,
        user_fields: Optional[List[str]] = None,
        expansions: Optional[List[str]] = None,
        place_fields: Optional[List[str]] = None,
        max_tweets: int = DEFAULT_MAX_TWEETS,
    ):
        self.twitter_config_filename = twitter_config_filename

        self.query = query
        self.keywords = keywords
        self.hashtags = hashtags
        self.usernames = usernames
        self.operators = operators if operators is not None else DEFAULT_OPERATORS

        self.since_id = since_id
        self.until_id = until_id
        self.lookup_period = lookup_period

        self.tweet_fields = tweet_fields if tweet_fields is not None else DEFAULT_TWEET_FIELDS
        self.user_fields = user_fields if user_fields is not None else DEFAULT_USER_FIELDS
        self.expansions = expansions if expansions is not None else DEFAULT_EXPANSIONS
        self.place_fields = place_fields if place_fields is not None else DEFAULT_PLACE_FIELDS
        self.max_tweets = max_tweets


class TwitterSource(BaseSource):
    name = "Twitter"

    def lookup(self, config: TwitterSourceConfig) -> List[SourceResponse]:
        if not config.query and not config.keywords and not config.hashtags and config.usernames:
            raise AttributeError("At least one non empty parameter required (query, keywords, hashtags, and usernames)")

        search_args = load_credentials(filename=config.twitter_config_filename, env_overwrite=True)

        place_fields = ",".join(config.place_fields) if config.place_fields is not None else None
        user_fields = ",".join(config.user_fields) if config.user_fields is not None else None
        expansions = ",".join(config.expansions) if config.expansions is not None else None
        tweet_fields = ",".join(config.tweet_fields) if config.tweet_fields is not None else None

        query = self._generate_query_string(
            query=config.query,
            keywords=config.keywords,
            hashtags=config.hashtags,
            usernames=config.usernames,
            operators=config.operators
        )

        search_query = gen_request_parameters(
            query=query,
            results_per_call=config.max_tweets,
            place_fields=place_fields,
            expansions=expansions,
            user_fields=user_fields,
            tweet_fields=tweet_fields,
            since_id=config.since_id,
            until_id=config.until_id,
            start_time=config.lookup_period
        )

        tweets_output = collect_results(
            query=search_query,
            max_tweets=config.max_tweets,
            result_stream_args=search_args
        )

        if not tweets_output:
            logger.info("No Tweets found")
            return []

        tweets = []
        users = []
        meta_info = None
        for raw_output in tweets_output:
            if "text" in raw_output:
                tweets.append(raw_output)
            elif "users" in raw_output:
                users = raw_output["users"]
            elif "meta" in raw_output:
                meta_info = raw_output["meta"]

        # Extract user info and create user map
        user_map: Dict[str, Dict[str, Any]] = {}
        if len(users) > 0 and "id" in users[0]:
            for user in users:
                user_map[user["id"]] = user

        # TODO use it later
        logger.info(f"Twitter API meta_info='{meta_info}'")

        source_responses: List[SourceResponse] = []
        for tweet in tweets:
            if "author_id" in tweet and tweet["author_id"] in user_map:
                tweet["author_info"] = user_map.get(tweet["author_id"])

            source_responses.append(self.get_source_output(tweet))

        return source_responses

    @staticmethod
    def _generate_query_string(
            query: str = None,
            keywords: List[str] = None,
            hashtags: List[str] = None,
            usernames: List[str] = None,
            operators: List[str] = None,
    ) -> str:
        if query:
            return query

        or_tokens = []
        and_tokens = []

        or_tokens_list = [keywords, hashtags, usernames]
        for tokens in or_tokens_list:
            if tokens:
                if len(tokens) > 0:
                    or_tokens.append(f'({" OR ".join(tokens)})')
                else:
                    or_tokens.append(f'{"".join(tokens)}')

        and_query_str = ""
        or_query_str = ""

        if or_tokens:
            if len(or_tokens) > 0:
                or_query_str = f'{" OR ".join(or_tokens)}'
            else:
                or_query_str = f'{"".join(or_tokens)}'

        if operators:
            and_tokens.append(f'{" ".join(operators)}')

        if and_tokens:
            and_query_str = f' ({" ".join(and_tokens)})' if and_tokens else ''

        return or_query_str + and_query_str

    def get_source_output(self, tweet: Dict[str, Any]):
        tweet_url = TwitterSource.get_tweet_url(tweet["text"])
        processed_text = TwitterSource.clean_tweet_text(tweet["text"])

        tweet["tweet_url"] = tweet_url
        return SourceResponse(
            processed_text=processed_text,
            meta=tweet,
            source_name=self.name
        )

    @staticmethod
    def clean_tweet_text(tweet_text: str):
        return cleaning_processor.clean(tweet_text)

    @staticmethod
    def get_tweet_url(tweet_text: str):
        parsed_tweet = cleaning_processor.parse(tweet_text)
        tweet_url = None
        if not parsed_tweet.urls:
            return tweet_url

        last_index = len(tweet_text)
        for url_info in parsed_tweet.urls:
            if url_info.end_index == last_index:
                tweet_url = url_info.match
                break

        return tweet_url
