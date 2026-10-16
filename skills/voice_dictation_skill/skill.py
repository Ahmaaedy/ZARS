import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for

def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    try:
        powershell_cmd = f"""
& {{
    $language = '{ns.language or "en-US"}';
    $continuous = {('$true' if ns.continuous else '$false')};
    $sensitivity = {ns.sensitivity if ns.sensitivity is not None else 0.5};
    Add-Type -TypeDefinition @'
        using System;
        using System.Speech.Recognition;
        using System.Globalization;
        using System.Windows.Forms;
        public class SpeechDictation {{
            public static void Run(string language, bool continuous, float sensitivity)
            {{
                var culture = new System.Globalization.CultureInfo(language);
                using (var recognizer = new SpeechRecognitionEngine(culture))
                {{
                    recognizer.SetInputToMicrophone();
                    // Load stop phrase grammar
                    var stopGrammar = new Grammar(new GrammarBuilder(new string[] {{ "that's the end" }}));
                    recognizer.LoadGrammar(stopGrammar);
                    // Load dictation grammar
                    var dictationGrammar = new DictationGrammar();
                    recognizer.LoadGrammar(dictationGrammar);
                    if (continuous)
                    {{
                        while (true)
                        {{
                            var result = recognizer.Recognize();
                            if (result == null) break;
                            string text = result.Text;
                            if (string.Equals(text, "that's the end", StringComparison.OrdinalIgnoreCase))
                                break;
                            SendKeys.Send(text);
                            SendKeys.Send(" ");
                        }}
                    }}
                    else
                    {{
                        var result = recognizer.Recognize();
                        if (result != null)
                        {{
                            string text = result.Text;
                            if (!string.Equals(text, "that's the end", StringComparison.OrdinalIgnoreCase))
                            {{
                                SendKeys.Send(text);
                                SendKeys.Send(" ");
                            }}
                        }}
                    }}
                }}
            }}
        }}
    '@;
    [SpeechDictation]::Run($language, $continuous, $sensitivity);
}}
"""
        result = subprocess.run(
            ["powershell", "-Command", powershell_cmd],
            capture_output=True, text=True, timeout=30
        )
        emit(True, result.stdout.strip())
    except Exception as e:
        emit(False, str(e))

if __name__ == "__main__":
    main()