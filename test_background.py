import base64

def image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        encoded = base64.b64encode(image_file.read()).decode('utf-8')
        # Determine the image type
        if image_path.endswith('.png'):
            return f"data:image/png;base64,{encoded}"
        elif image_path.endswith('.jpg') or image_path.endswith('.jpeg'):
            return f"data:image/jpeg;base64,{encoded}"
        else:
            return f"data:image/png;base64,{encoded}"

# Use this base64 string directly in your Flow JSON
logo_base64 = image_to_base64("Flean_Logo_White_BG_SQ.jpg")
print(logo_base64)