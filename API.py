from openai import OpenAI
import sys
from pathlib import Path
import utils as U


class API:
    def __init__(self, config):
        self.config = config
        self.client = None
        self.api_key = None
        self.url = None
        self.model = None
        self.init()

    def init(self):
        self.api_key = self.config['provider']['api_key']
        self.url = self._normalize_base_url(self.config['provider']['url'])
        self.model = self.config['provider']['model']

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.url,
        )

    def _normalize_base_url(self, url):
        if not url or not isinstance(url, str):
            return url
        normalized = url.strip()
        normalized = normalized.rstrip('/')
        if normalized.endswith('/chat/completions'):
            normalized = normalized[:-len('/chat/completions')]
        return normalized

    def get_response(self, msgs):
        '''
        无论给谁，问 msgs，返回 response
        '''
        if not self.client:
            print('client 未正确初始化，无法请求模型')
            return None

        model_config = self.config.get('model', {})
        payload = {
            'model': self.model,
            'messages': msgs,
        }

        optional_fields = ['frequency_penalty', 'max_tokens', 'temperature', 'top_p', 'n']
        for field in optional_fields:
            value = model_config.get(field)
            if value is not None:
                payload[field] = value

        try:
            completion = self.client.chat.completions.create(**payload)
            if not completion.choices:
                return None
            response = completion.choices[0].message.content or ''
            return response.lstrip('\n')
        except Exception as e:
            print(f'获取回复报错：{e}')
            return None


    def sending_list(self, msgs: list):
        print(f'Model 输入：')
        for msg in msgs:
            print(U.ZIP(msg['content']) + ' ---- ' + msg['role'])

        response = self.get_response(msgs)
        # print(f'response is : {response}')

        if not response:
            print('获取到的response有误')
            return None
        return response


if __name__ == "__main__":
    import yaml
    def load_yaml(file_path):
        with open(file_path, 'r', encoding='utf-8') as file:
            y = yaml.safe_load(file)
        return y
    plugin_root = Path(__file__).resolve().parent
    config = load_yaml(plugin_root / 'config' / 'config.yaml')
    api = API(config=config['llm'])
    response = api.sending_list([{
        'role': 'user',
        'content': '你好呀',
    }])
    print(response)


