import json

import requests

MESSAGING_API_ENDPOINT = "https://api.gradio.app/gradio-messaging/en"

en = {
    "RUNNING_LOCALLY": "Running on local URL:  {}",
    "RUNNING_LOCALLY_SEPARATED": "Running on local URL:  {}://{}:{}",
    "SHARE_LINK_DISPLAY": "Running on public URL: {}",
    "COULD_NOT_GET_SHARE_LINK": "\nCould not create share link, please check your internet connection.",
    "COLAB_NO_LOCAL": "Cannot display local interface on google colab, public link created.",
    "PUBLIC_SHARE_TRUE": "\nTo create a public link, set `share=True` in `launch()`.",
    "MODEL_PUBLICLY_AVAILABLE_URL": "Model available publicly at: {} (may take up to a minute for link to be usable)",
    "GENERATING_PUBLIC_LINK": "Generating public link (may take a few seconds...):",
    "TF1_ERROR": "It looks like you might be using tensorflow < 2.0. Please pass capture_session=True in Interface() to"
    " avoid the 'Tensor is not an element of this graph.' error.",
    "BETA_INVITE": "\nThanks for being a Gradio user! If you have questions or feedback, please join our Discord server and chat with us: https://discord.gg/feTf9x3ZSB",
    "COLAB_DEBUG_TRUE": "Colab notebook detected. This cell will run indefinitely so that you can see errors and logs. "
    "To turn off, set debug=False in launch().",
    "COLAB_DEBUG_FALSE": "Colab notebook detected. To show errors in colab notebook, set debug=True in launch()",
    "COLAB_BETA": "\nUsing Embedded Colab Mode (NEW). If you have issues, please use share=True and file an issue at https://github.com/gradio-app/gradio/"
    "\nNote: opening the browser inspector may crash Embedded Colab Mode.",
    "SHARE_LINK_MESSAGE": "\nThis share link expires in 72 hours. For free permanent hosting and GPU upgrades (NEW!), check out Spaces: https://huggingface.co/spaces",
    "PRIVATE_LINK_MESSAGE": "Since this is a private endpoint, this share link will never expire.",
    "INLINE_DISPLAY_BELOW": "Interface loading below...",
    "TIPS": [
        "You can add authentication to your app with the `auth=` kwarg in the `launch()` command; for example: `gr.Interface(...).launch(auth=('username', 'password'))`",
        "Let users specify why they flagged input with the `flagging_options=` kwarg; for example: `gr.Interface(..., flagging_options=['too slow', 'incorrect output', 'other'])`",
        "You can show or hide the button for flagging with the `allow_flagging=` kwarg; for example: gr.Interface(..., allow_flagging=False)",
        "The inputs and outputs flagged by the users are stored in the flagging directory, specified by the flagging_dir= kwarg. You can view this data through the interface by setting the examples= kwarg to the flagging directory; for example gr.Interface(..., examples='flagged')",
        "You can add a title and description to your interface using the `title=` and `description=` kwargs. The `article=` kwarg can be used to add a description under the interface; for example gr.Interface(..., title='My app', description='Lorem ipsum'). Try using Markdown!",
        "For a classification or regression model, set `interpretation='default'` to see why the model made a prediction.",
    ],
}

try:
    updated_messaging = requests.get(MESSAGING_API_ENDPOINT, timeout=3).json()
    en.update(updated_messaging)
except (
    requests.ConnectionError,
    requests.exceptions.ReadTimeout,
    json.decoder.JSONDecodeError,
):  # Use default messaging
    pass
