import React, { useState } from 'react';
import { Input, Button, Typography, Row, Col, Select } from 'antd';
import { ArrowRightOutlined } from '@ant-design/icons';
import './index.less';

const { Title } = Typography;

interface WelcomeProps {
  onSubmit: (value: string) => void;
  models: Array<{ label: string; value: string }>;
  selectedModel: string;
  onModelChange: (value: string) => void;
  modelsLoading: boolean;
}

const Welcome: React.FC<WelcomeProps> = ({
  onSubmit,
  models,
  selectedModel,
  onModelChange,
  modelsLoading,
}) => {
  const [inputValue, setInputValue] = useState('');
  
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSubmit(inputValue);
    }
  };

  return (
    <div className="welcome-container">
      <div className="content">
        <Row justify="center">
          <Col>
            <Title level={1}>Hello, Aworld</Title>
          </Col>
        </Row>
        <div className="input-area">
          <Input.TextArea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask or input / use skills"
            autoSize={{ minRows: 3, maxRows: 5 }}
            className="text-input"
          />
          <div className="controls-area">
            <Select
              value={selectedModel}
              onChange={onModelChange}
              options={models}
              loading={modelsLoading}
              placeholder="Select a model"
              className="model-select"
              showSearch
              filterOption={(input, option) =>
                (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
              }
            />
          </div>
          <Button
            type="primary"
            shape="circle"
            onClick={() => onSubmit(inputValue)}
            icon={<ArrowRightOutlined />}
            className="submit-button"
          />
        </div>
      </div>
    </div>
  );
};

export default Welcome;
