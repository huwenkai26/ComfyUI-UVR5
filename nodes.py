import os
import random
import torch
import ffmpeg
import hashlib
import folder_paths
import requests
import urllib.request
import urllib.parse
import urllib.error
import tempfile
import uuid
from huggingface_hub import hf_hub_download
from .uvr5.mdxnet import MDXNetDereverb
from .uvr5.vr import AudioPre, AudioPreDeEcho
from cuda_malloc import cuda_malloc_supported
from urllib.parse import urlparse
import torchaudio

input_path = folder_paths.get_input_directory()
output_path = folder_paths.get_output_directory()
base_path = os.path.dirname(input_path)
node_path = os.path.join(base_path,"custom_nodes/ComfyUI-UVR5")
weights_path = os.path.join(node_path, "uvr5")
device= "cuda" if cuda_malloc_supported() else "cpu"
is_half=True
AUDIO_EXTENSIONS = ['.mp3', '.wav', '.flac', '.ogg', '.m4a']

class AudioURLLoader:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "url": ("STRING", {"default": "https://example.com/audio.mp3"}),
            }
        }

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "media_utilities_load_audio"
    CATEGORY = "AIFSH_UVR5"

    def media_utilities_load_audio(self, url):
        try:
            # Check if URL is valid
            parsed_url = urlparse(url)
            if not parsed_url.scheme or not parsed_url.netloc:
                raise ValueError(f"Invalid URL: {url}")

            # Download audio file
            response = requests.get(url, stream=True)
            response.raise_for_status()

            # Create temporary file to save the audio
            extension = os.path.splitext(parsed_url.path)[1].lower()
            if extension not in AUDIO_EXTENSIONS:
                extension = '.mp3'  # Default extension if not recognized

            with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as temp_file:
                temp_path = temp_file.name
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        temp_file.write(chunk)

            # Load audio using torchaudio
            waveform, sample_rate = torchaudio.load(temp_path)

            # Cleanup temporary file
            os.unlink(temp_path)

            # Return audio in ComfyUI format
            return ({"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate},)

        except Exception as e:
            print(f"Error loading audio from URL: {str(e)}")
            # Return empty audio in case of error
            waveform = torch.zeros((1, 2, 1))
            sample_rate = 44100
            return ({"waveform": waveform, "sample_rate": sample_rate},)

class PreViewAudio:
    @classmethod
    def INPUT_TYPES(s):
        return {"required":
                    {"audio": ("AUDIOPATH",),}
                }

    CATEGORY = "AIFSH_UVR5"
    DESCRIPTION = "hello world!"

    RETURN_TYPES = ()

    OUTPUT_NODE = True

    FUNCTION = "load_audio"

    def load_audio(self, audio):
        audio_name = os.path.basename(audio)
        tmp_path = os.path.dirname(audio)
        audio_root = os.path.basename(tmp_path)
        return {"ui": {"audio":[audio_name,audio_root]}}


    @classmethod
    def IS_CHANGED(s, audio):
        audio_path = folder_paths.get_annotated_filepath(audio)
        m = hashlib.sha256()
        with open(audio_path, 'rb') as f:
            m.update(f.read())
        return m.digest().hex()

class LoadAudioPath:
    @classmethod
    def INPUT_TYPES(s):
        input_dir = input_path
        files = [f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f)) and f.split('.')[-1].lower() in ["wav", "mp3","flac","m4a"]]
        return {"required":
                    {"source_type": (["file", "url"], {"default": "url"}),
                     "audio": ("STRING", {"default": ""}),
                    },
               }

    CATEGORY = "AIFSH_UVR5"

    RETURN_TYPES = ("AUDIOPATH",)
    FUNCTION = "load_audio"

    def load_audio(self, source_type, audio, _audio_changed=False):
        if source_type == "file":
            audio_path = folder_paths.get_annotated_filepath(audio)
            return (audio_path,)
        else:
            # 处理URL
            try:
                # 生成文件名
                url = audio
                url_filename = os.path.basename(urllib.parse.urlparse(url).path)
                if not url_filename or "." not in url_filename:
                    # URL没有有效文件名，生成随机文件名
                    ext = ".mp3"
                    url_filename = f"{uuid.uuid4()}{ext}"

                # 确保文件扩展名正确
                file_ext = os.path.splitext(url_filename)[1].lower()
                if file_ext not in [".wav", ".mp3", ".flac", ".m4a"]:
                    url_filename = f"{os.path.splitext(url_filename)[0]}.mp3"

                save_path = os.path.join(input_path, url_filename)

                # 下载文件
                print(f"下载音频文件: {url} 到 {save_path}")
                urllib.request.urlretrieve(url, save_path)

                return (save_path,)
            except Exception as e:
                print(f"下载音频文件失败: {str(e)}")
                raise RuntimeError(f"无法从URL下载音频: {str(e)}")

    @classmethod
    def IS_CHANGED(s, source_type, audio, url, _audio_changed=False):
        if source_type == "file":
            audio_path = folder_paths.get_annotated_filepath(audio)
            m = hashlib.sha256()
            with open(audio_path, 'rb') as f:
                m.update(f.read())
            return m.digest().hex()
        else:
            # URL模式下，只要URL变化就重新执行
            return url
class UVR5:
    """
    A example node

    Class methods
    -------------
    INPUT_TYPES (dict):
        Tell the main program input parameters of nodes.
    IS_CHANGED:
        optional method to control when the node is re executed.

    Attributes
    ----------
    RETURN_TYPES (`tuple`):
        The type of each element in the output tulple.
    RETURN_NAMES (`tuple`):
        Optional: The name of each output in the output tulple.
    FUNCTION (`str`):
        The name of the entry-point method. For example, if `FUNCTION = "execute"` then it will run Example().execute()
    OUTPUT_NODE ([`bool`]):
        If this node is an output node that outputs a result/image from the graph. The SaveImage node is an example.
        The backend iterates on these output nodes and tries to execute all their parents if their parent graph is properly connected.
        Assumed to be False if not present.
    CATEGORY (`str`):
        The category the node should appear in the UI.
    execute(s) -> tuple || None:
        The entry point method. The name of this method must be the same as the value of property `FUNCTION`.
        For example, if `FUNCTION = "execute"` then this method's name must be `execute`, if `FUNCTION = "foo"` then it must be `foo`.
    """
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        """
            Return a dictionary which contains config for all input fields.
            Some types (string): "MODEL", "VAE", "CLIP", "CONDITIONING", "LATENT", "IMAGE", "INT", "STRING", "FLOAT".
            Input types "INT", "STRING" or "FLOAT" are special values for fields on the node.
            The type can be a list for selection.

            Returns: `dict`:
                - Key input_fields_group (`string`): Can be either required, hidden or optional. A node class must have property `required`
                - Value input_fields (`dict`): Contains input fields config:
                    * Key field_name (`string`): Name of a entry-point method's argument
                    * Value field_config (`tuple`):
                        + First value is a string indicate the type of field or a list for selection.
                        + Secound value is a config for type "INT", "STRING" or "FLOAT".
        """
        model_list = ["HP5_only_main_vocal.pth","HP5-主旋律人声vocals+其他instrumentals.pth","HP5_only_main_vocal.pth",
                      "HP2_all_vocals.pth","HP2-人声vocals+非人声instrumentals.pth","HP3_all_vocals.pth",
                      "VR-DeEchoAggressive.pth","VR-DeEchoDeReverb.pth","VR-DeEchoNormal.pth","onnx_dereverb_By_FoxJoy"]
        return {
            "required": {
                "audio": ("AUDIOPATH",),
                "model": (model_list,{
                    "default": "HP5-主旋律人声vocals+其他instrumentals.pth"
                }),
                "agg":("INT",{
                    "default": 10,
                    "min": 0, #Minimum value
                    "max": 20, #Maximum value
                    "step": 1, #Slider's step
                    "display": "slider"
                }),
                "format0":(["wav", "flac", "mp3", "m4a"],{
                    "default": "wav"
                })
            },
        }

    RETURN_TYPES = ("AUDIOPATH","AUDIOPATH")
    RETURN_NAMES = ("vocal_AUDIO","bgm_AUDIO")

    FUNCTION = "split"

    #OUTPUT_NODE = False

    CATEGORY = "AIFSH_UVR5"

    def split(self, audio, model,agg,format0):

        if model == "onnx_dereverb_By_FoxJoy":
            if not os.path.isfile(os.path.join(weights_path,"uvr5_weights/onnx_dereverb_By_FoxJoy", "vocals.onnx")):
                hf_hub_download(
                    repo_id="lj1995/VoiceConversionWebUI",
                    filename="vocals.onnx",
                    subfolder= "uvr5_weights/onnx_dereverb_By_FoxJoy",
                    local_dir= weights_path
                )
        else:
            if not os.path.isfile(os.path.join(weights_path,"uvr5_weights", model)):
                hf_hub_download(
                    repo_id="lj1995/VoiceConversionWebUI",
                    filename=model,
                    subfolder= "uvr5_weights",
                    local_dir= weights_path
                )
        save_root_vocal = output_path
        save_root_ins = output_path
        vocal_AUDIO,bgm_AUDIO = self.uvr(model, audio, save_root_vocal,save_root_ins,agg, format0)
        return (vocal_AUDIO,bgm_AUDIO,)

    def uvr(self, model_name, inp_root, save_root_vocal,save_root_ins, agg, format0):
        vocal_AUDIO,bgm_AUDIO = "", ""
        inp_root = inp_root.strip(" ").strip('"').strip("\n").strip('"').strip(" ")
        save_root_vocal = (
            save_root_vocal.strip(" ").strip('"').strip("\n").strip('"').strip(" ")
        )
        save_root_ins = (
            save_root_ins.strip(" ").strip('"').strip("\n").strip('"').strip(" ")
        )
        is_hp3 = "HP3" in model_name
        if model_name == "onnx_dereverb_By_FoxJoy":
            pre_fun = MDXNetDereverb(15)
        else:
            func = AudioPre if "DeEcho" not in model_name else AudioPreDeEcho
            pre_fun = func(
                agg=int(agg),
                model_path=os.path.join(weights_path, "uvr5_weights",model_name),
                device=device,
                is_half=is_half,
            )
        inp_path = inp_root
        need_reformat = 1
        done = 0

        info = ffmpeg.probe(inp_path, cmd="ffprobe")
        if (
            info["streams"][0]["channels"] == 2
            and info["streams"][0]["sample_rate"] == "44100"
        ):
            need_reformat = 0
            vocal_AUDIO,bgm_AUDIO = pre_fun._path_audio_(
                inp_path, save_root_ins, save_root_vocal, format0,is_hp3
            )
            done = 1
        else:
            need_reformat = 1

        if need_reformat == 1:
            tmp_path = "%s/%s.reformatted.wav" % (
                input_path,
                os.path.basename(inp_path),
            )
            os.system(
                f'ffmpeg -i "{inp_path}" -vn -acodec pcm_s16le -ac 2 -ar 44100 "{tmp_path}" -y'
            )
            inp_path = tmp_path

        if done == 0:
            vocal_AUDIO,bgm_AUDIO = pre_fun._path_audio_(
                inp_path, save_root_ins, save_root_vocal, format0,is_hp3
            )
            print("%s->Success" % (os.path.basename(inp_path)))

        try:
            if model_name == "onnx_dereverb_By_FoxJoy":
                del pre_fun.pred.model
                del pre_fun.pred.model_
            else:
                del pre_fun.model
                del pre_fun
        except:
            pass
        print("clean_empty_cache")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return vocal_AUDIO,bgm_AUDIO
    """
        The node will always be re executed if any of the inputs change but
        this method can be used to force the node to execute again even when the inputs don't change.
        You can make this node return a number or a string. This value will be compared to the one returned the last time the node was
        executed, if it is different the node will be executed again.
        This method is used in the core repo for the LoadImage node where they return the image hash as a string, if the image hash
        changes between executions the LoadImage node is executed again.
    """

