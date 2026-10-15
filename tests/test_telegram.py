import importlib.util
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('telegram_test_module',Path(__file__).resolve().parents[1]/'telegram_bot.py')
mod=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{'requests':SimpleNamespace()}): spec.loader.exec_module(mod)

class Pairing(unittest.TestCase):
    def test_only_private_pairing_code_can_claim_owner(self):
        with tempfile.TemporaryDirectory() as d:
            bot=mod.Bot('FAKE-NOT-A-TOKEN',root=Path(d))
            sent=[];bot.say=lambda text,chat=None:sent.append(text)
            def msg(chat_id,text,kind='private'):
                return {'message':{'chat':{'id':chat_id,'type':kind},'text':text}}
            bot.handle(msg(1,'/start'))
            self.assertNotIn('owner',bot.config)
            bot.handle(msg(1,'/start '+bot.code,'group'))
            self.assertNotIn('owner',bot.config)
            bot.handle(msg(1,'/start '+bot.code))
            self.assertEqual(bot.config['owner'],1)
            before=len(sent)
            bot.handle(msg(2,'/status'))
            self.assertEqual(len(sent),before)
            bot.handle(msg(1,'/status'))
            self.assertEqual(len(sent),before+1)
            self.assertNotIn('FAKE-NOT-A-TOKEN',bot.settings.read_text())

if __name__=='__main__':unittest.main()
